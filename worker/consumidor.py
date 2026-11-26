"""
Consumo da fila: texto -> embedding (inferencia) -> gravacao (vetorial).

Politica de falhas:
  circuito aberto    -> espera e devolve a fila SEM gastar tentativa
  erro transitorio   -> devolve a fila com tentativas+1; no limite vai para a DLQ
  erro permanente    -> DLQ na hora (repetir nao resolveria)
O XACK so acontece depois de persistir ou de a mensagem estar salva em outro lugar.
"""
import hashlib
import json
import logging
import os
import time

import redis
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from comum import fila
from comum.circuit_breaker import CircuitoAberto
from comum.config import retencao_segundos
from comum.retry import e_transitorio
from comum.telemetria import extrair_contexto

log = logging.getLogger("plataforma.worker")
tracer = trace.get_tracer("worker")

MAX_TENTATIVAS = int(os.getenv("FILA_MAX_TENTATIVAS", "5"))
RECLAMAR_APOS_MS = int(os.getenv("FILA_RECLAMAR_APOS_MS", "180000"))


def id_documento(texto):
    return hashlib.sha256(" ".join(texto.split()).encode("utf-8")).hexdigest()


def indexar(inferencia, vetorial, usuario, textos, criado_em):
    vetores = inferencia.chamar("POST", "/embeddings", json={"textos": textos})["vetores"]
    itens = [{"id": id_documento(t), "texto": t, "vetor": v}
             for t, v in zip(textos, vetores)]
    return vetorial.chamar("PUT", "/itens", json={
        "usuario": usuario, "criado_em": criado_em, "itens": itens})


def _concluir(r, msg_id):
    r.xack(fila.STREAM, fila.GRUPO, msg_id)
    r.xdel(fila.STREAM, msg_id)


def _marcar(r, lote, **campos):
    # so atualiza lote que ainda existe, para nao recriar chave sem TTL
    if r.exists(fila.chave_lote(lote)):
        r.hset(fila.chave_lote(lote), mapping=campos)


def processar(r, inferencia, vetorial, msg_id, campos, dormir=time.sleep):
    # o span e filho do "fila publicar" do gateway: o trace atravessa a fila
    with tracer.start_as_current_span("worker processar lote", kind=SpanKind.CONSUMER,
                                      context=extrair_contexto(campos)) as span:
        lote = campos.get("lote", "?")
        span.set_attribute("lote.id", lote)
        resultado = _processar(r, inferencia, vetorial, msg_id, campos, lote, dormir)
        span.set_attribute("lote.resultado", resultado)
        if resultado in ("falhou", "reenfileirado"):
            span.set_status(Status(StatusCode.ERROR))
        return resultado


def _processar(r, inferencia, vetorial, msg_id, campos, lote, dormir):
    tentativas = int(campos.get("tentativas", 0))
    try:
        criado_em = float(campos["criado_em"])
        if time.time() - criado_em > retencao_segundos():
            _concluir(r, msg_id)
            _marcar(r, lote, estado="expirado")
            return "expirado"
        textos = json.loads(campos["textos"])
        _marcar(r, lote, estado="processando")
        resposta = indexar(inferencia, vetorial, campos["usuario"], textos, criado_em)
    except CircuitoAberto as e:
        log.warning("lote %s adiado: circuito aberto", lote)
        dormir(min(max(e.retry_apos, 1.0), 30.0))
        _devolver(r, msg_id, campos, lote, tentativas)
        return "adiado"
    except redis.RedisError:
        raise  # sem Redis nao ha o que fazer: a mensagem fica sem ack e volta depois
    except Exception as e:
        tentativas += 1
        erro = type(e).__name__  # sem a mensagem: ela pode conter dados do usuario
        if e_transitorio(e) and tentativas < MAX_TENTATIVAS:
            log.warning("lote %s falhou (%s), tentativa %d/%d", lote, erro,
                        tentativas, MAX_TENTATIVAS)
            dormir(min(2 ** tentativas, 20))
            _devolver(r, msg_id, campos, lote, tentativas, erro)
            return "reenfileirado"
        log.error("lote %s enviado para a DLQ (%s)", lote, erro)
        if _ainda_existe(r, msg_id):
            r.xadd(fila.DLQ, {**campos, "tentativas": tentativas, "erro": erro})
            r.expire(fila.DLQ, retencao_segundos())
        _concluir(r, msg_id)
        _marcar(r, lote, estado="falhou", tentativas=tentativas, erro=erro)
        return "falhou"
    _marcar(r, lote, estado="concluido", gravados=resposta["gravados"], erro="")
    _concluir(r, msg_id)  # ack somente apos persistir
    log.info("lote %s concluido (%d itens)", lote, resposta["gravados"])
    return "concluido"


def _ainda_existe(r, msg_id):
    # se o usuario pediu eliminacao durante a espera, a mensagem sumiu: nao recriar
    return bool(r.xrange(fila.STREAM, min=msg_id, max=msg_id))


def _devolver(r, msg_id, campos, lote, tentativas, erro=""):
    if not _ainda_existe(r, msg_id):
        r.xack(fila.STREAM, fila.GRUPO, msg_id)
        return
    with r.pipeline(transaction=True) as p:
        p.xadd(fila.STREAM, {**campos, "tentativas": tentativas})
        p.xack(fila.STREAM, fila.GRUPO, msg_id)
        p.xdel(fila.STREAM, msg_id)
        p.execute()
    _marcar(r, lote, estado="pendente", tentativas=tentativas, erro=erro)


def ciclo(r, inferencia, vetorial, nome, bloquear_ms=2000):
    """Uma volta do laco: aplica retencao, reclama orfas e consome uma mensagem."""
    fila.expurgar_antigas(r)
    # mensagens entregues a um worker que morreu antes do ack
    reclamadas = r.xautoclaim(fila.STREAM, fila.GRUPO, nome,
                              min_idle_time=RECLAMAR_APOS_MS, count=1)[1]
    for msg_id, campos in reclamadas:
        if campos:
            processar(r, inferencia, vetorial, msg_id, campos)
    lidos = r.xreadgroup(fila.GRUPO, nome, {fila.STREAM: ">"}, count=1, block=bloquear_ms)
    for _, mensagens in lidos or []:
        for msg_id, campos in mensagens:
            processar(r, inferencia, vetorial, msg_id, campos)


def executar(inferencia, vetorial, nome, parar, batimento, conectar=fila.conectar):
    while not parar.is_set():
        try:
            r = conectar()
            fila.garantir_grupo(r)
            while not parar.is_set():
                batimento()
                ciclo(r, inferencia, vetorial, nome)
        except redis.RedisError as e:
            log.error("redis indisponivel (%s); nova tentativa em 2s", type(e).__name__)
            parar.wait(2)
        except Exception:  # o consumidor nao pode morrer em silencio
            log.exception("erro inesperado no consumidor")
            parar.wait(2)
