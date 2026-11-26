"""
Fila de indexacao sobre Redis Streams (consumer group), adaptada do C2.A2.

  fila:documentos       trabalhos pendentes
  fila:documentos:dlq   trabalhos que falharam em definitivo (fila de mortos)
  lote:<id>             status do lote (expira junto com a retencao)

Entrega "ao menos uma vez": o XACK so acontece depois de persistir. Como o id do
documento e um hash do conteudo, reprocessar e inofensivo.
"""
import json
import os
import time
import uuid

import redis

from comum.config import retencao_segundos

STREAM = "fila:documentos"
DLQ = "fila:documentos:dlq"
GRUPO = "grupo-indexacao"


def conectar(url=None):
    return redis.Redis.from_url(url or os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=10)


def chave_lote(lote):
    return f"lote:{lote}"


def garantir_grupo(r):
    try:
        r.xgroup_create(STREAM, GRUPO, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def publicar(r, usuario, textos, contexto_trace=None):
    """Enfileira um lote. Status e mensagem sao gravados na mesma transacao."""
    lote = uuid.uuid4().hex
    with r.pipeline(transaction=True) as p:
        p.hset(chave_lote(lote), mapping={"estado": "pendente", "usuario": usuario,
                                          "documentos": len(textos), "tentativas": 0})
        p.expire(chave_lote(lote), retencao_segundos())
        p.xadd(STREAM, {"lote": lote, "usuario": usuario, "textos": json.dumps(textos),
                        "tentativas": 0, "criado_em": repr(time.time()),
                        **(contexto_trace or {})})
        # a chave inteira expira apos a ultima escrita: a retencao vale mesmo
        # com o worker parado ou hibernado
        p.expire(STREAM, retencao_segundos())
        p.execute()
    return lote


def status(r, lote):
    return r.hgetall(chave_lote(lote)) or None


def metricas(r):
    sem_ack = 0
    try:
        sem_ack = r.xpending(STREAM, GRUPO)["pending"]
    except redis.ResponseError:  # grupo ainda nao existe
        pass
    return {"na_fila": r.xlen(STREAM), "sem_ack": sem_ack, "mortos": r.xlen(DLQ)}


def apagar_do_usuario(r, usuario):
    """Direito de eliminacao (LGPD): remove mensagens do usuario da fila e da DLQ."""
    removidas = 0
    for stream in (STREAM, DLQ):
        ids = [i for i, campos in r.xrange(stream) if campos.get("usuario") == usuario]
        if ids:
            removidas += r.xdel(stream, *ids)
    return removidas


def expurgar_antigas(r, agora=None):
    """Aplica a retencao: descarta da fila e da DLQ o que passou do prazo."""
    corte = int(((agora or time.time()) - retencao_segundos()) * 1000)
    for stream in (STREAM, DLQ):
        r.xtrim(stream, minid=str(corte), approximate=False)
