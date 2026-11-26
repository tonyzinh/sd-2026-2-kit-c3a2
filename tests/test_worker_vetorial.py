import time

import pytest
import requests
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from comum import fila
from comum.circuit_breaker import CircuitoAberto
from comum.telemetria import configurar_telemetria, contexto_atual
from inferencia.embeddings import vetorizar
from tests.conftest import erro_http
from vetorial import app as app_vetorial
from vetorial import armazem
from worker import consumidor


class Inferencia:
    def __init__(self, erro=None):
        self.erro = erro

    def chamar(self, metodo, caminho, json):
        if self.erro:
            raise self.erro
        return {"vetores": vetorizar(json["textos"])}


class Vetorial:
    def __init__(self, banco):
        self.banco = banco

    def chamar(self, metodo, caminho, json):
        return {"gravados": armazem.gravar(self.banco, json["usuario"], json["itens"],
                                           json["criado_em"])}


def pegar(r):
    fila.garantir_grupo(r)
    [(_, [(msg_id, campos)])] = r.xreadgroup(fila.GRUPO, "c1", {fila.STREAM: ">"}, count=1)
    return msg_id, campos


def processar(r, inferencia, msg):
    return consumidor.processar(r, inferencia, Vetorial(r), *msg, dormir=lambda s: None)


def pendentes(r):
    return r.xpending(fila.STREAM, fila.GRUPO)["pending"]


def test_sucesso_indexa_da_ack_e_aplica_ttl(r):
    lote = fila.publicar(r, "ana", ["o raft elege um lider"])
    assert processar(r, Inferencia(), pegar(r)) == "concluido"
    assert fila.status(r, lote)["estado"] == "concluido"
    assert pendentes(r) == 0 and r.xlen(fila.STREAM) == 0
    achados = armazem.buscar(r, "ana", vetorizar(["raft lider"])[0], 3)
    assert achados[0]["texto"] == "o raft elege um lider"
    assert r.ttl(f"vet:ana:{achados[0]['id']}") > 0


def test_reprocessar_e_idempotente(r):
    for _ in range(2):
        fila.publicar(r, "ana", ["mesmo texto"])
        processar(r, Inferencia(), pegar(r))
    assert armazem.total(r, "ana") == 1


def test_erro_transitorio_reenfileira_e_depois_vai_para_dlq(r):
    lote = fila.publicar(r, "ana", ["abc"])
    resultados = [processar(r, Inferencia(requests.ConnectionError()), pegar(r))
                  for _ in range(consumidor.MAX_TENTATIVAS)]
    assert resultados[-1] == "falhou" and set(resultados[:-1]) == {"reenfileirado"}
    assert r.xlen(fila.DLQ) == 1 and pendentes(r) == 0
    assert fila.status(r, lote)["estado"] == "falhou"


def test_erro_permanente_vai_direto_para_dlq(r):
    fila.publicar(r, "ana", ["abc"])
    assert processar(r, Inferencia(erro_http(422)), pegar(r)) == "falhou"
    assert r.xlen(fila.DLQ) == 1


def test_circuito_aberto_adia_sem_gastar_tentativa(r):
    lote = fila.publicar(r, "ana", ["abc"])
    assert processar(r, Inferencia(CircuitoAberto(retry_apos=1)), pegar(r)) == "adiado"
    assert fila.status(r, lote)["tentativas"] == "0"
    assert pegar(r)[1]["tentativas"] == "0"


def test_mensagem_antiga_e_descartada_pela_retencao(r, monkeypatch):
    lote = fila.publicar(r, "ana", ["abc"])
    msg_id, campos = pegar(r)
    campos["criado_em"] = str(time.time() - 25 * 3600)
    assert processar(r, Inferencia(), (msg_id, campos)) == "expirado"
    assert armazem.total(r, "ana") == 0 and fila.status(r, lote)["estado"] == "expirado"


def test_expurgo_limpa_a_dlq_vencida(r):
    r.xadd(fila.DLQ, {"usuario": "ana"})
    fila.expurgar_antigas(r)
    assert r.xlen(fila.DLQ) == 1
    fila.expurgar_antigas(r, agora=time.time() + 25 * 3600)
    assert r.xlen(fila.DLQ) == 0


def test_eliminacao_nao_e_desfeita_por_trabalho_em_andamento(r):
    fila.publicar(r, "ana", ["dado a eliminar"])
    msg = pegar(r)  # o worker ja esta com a mensagem
    armazem.apagar(r, "ana")
    fila.apagar_do_usuario(r, "ana")
    processar(r, Inferencia(), msg)
    assert armazem.total(r, "ana") == 0 and r.xlen(fila.STREAM) == 0


def test_eliminacao_simultanea_a_gravacao(r, monkeypatch):
    """O DELETE chega depois de a marca ser lida e antes de o vetor ser escrito."""
    original = armazem.json.dumps  # chamado entre a leitura da marca e o EXEC
    disparado = []

    def serializar_com_delete_no_meio(*args, **kwargs):
        if not disparado:
            disparado.append(True)
            armazem.apagar(r, "ana")
        return original(*args, **kwargs)

    criado_em = time.time() - 1
    monkeypatch.setattr(armazem.json, "dumps", serializar_com_delete_no_meio)
    item = {"id": "a" * 64, "texto": "dado", "vetor": [1.0]}
    assert armazem.gravar(r, "ana", [item], criado_em) == 0
    assert disparado and r.get("vet:ana:" + "a" * 64) is None


def test_filas_expiram_mesmo_sem_worker(r):
    fila.publicar(r, "ana", ["abc"])
    assert r.ttl(fila.STREAM) > 0
    processar(r, Inferencia(erro_http(422)), pegar(r))
    assert r.ttl(fila.DLQ) > 0


def test_eliminacao_durante_falha_nao_ressuscita_a_mensagem(r):
    fila.publicar(r, "ana", ["dado"])
    msg = pegar(r)
    fila.apagar_do_usuario(r, "ana")
    processar(r, Inferencia(requests.Timeout()), msg)
    assert r.xlen(fila.STREAM) == 0 and r.xlen(fila.DLQ) == 0


def test_busca_isola_usuarios(r):
    fila.publicar(r, "ana", ["segredo da ana sobre raft"])
    processar(r, Inferencia(), pegar(r))
    assert armazem.buscar(r, "bia", vetorizar(["raft"])[0], 3) == []


def test_vetorial_exige_token_interno(r, monkeypatch):
    monkeypatch.setattr(app_vetorial, "banco", r)
    cli = TestClient(app_vetorial.app)
    corpo = {"usuario": "ana", "vetor": [1.0]}
    assert cli.post("/buscar", json=corpo).status_code == 401
    assert cli.post("/buscar", json=corpo,
                    headers={"X-Token-Interno": "errado"}).status_code == 401
    assert cli.post("/buscar", json=corpo,
                    headers={"X-Token-Interno": "dev-token-interno"}).status_code == 200
    assert cli.delete("/usuarios/ana").status_code == 401
    assert cli.get("/saude").status_code == 200


def test_trace_atravessa_a_fila(r):
    configurar_telemetria("teste")
    memoria = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(memoria))
    with trace.get_tracer("t").start_as_current_span("fila publicar") as produtor:
        fila.publicar(r, "ana", ["abc"], contexto_atual())
    processar(r, Inferencia(), pegar(r))
    consumo = next(s for s in memoria.get_finished_spans()
                   if s.name == "worker processar lote")
    assert consumo.context.trace_id == produtor.get_span_context().trace_id
    assert consumo.parent.span_id == produtor.get_span_context().span_id
    assert "abc" not in str(dict(consumo.attributes))  # sem conteudo no trace
