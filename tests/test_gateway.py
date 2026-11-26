import time

import jwt
import pytest
import redis
import requests
from fastapi.testclient import TestClient

from comum import fila
from gateway import app as gw
from gateway import auth
from gateway.limite import Limitador
from tests.conftest import zerar


@pytest.fixture
def cli(r, monkeypatch):
    monkeypatch.setattr(gw, "banco", r)
    monkeypatch.setattr(gw, "limitador", Limitador(r))
    zerar(gw.inferencia.disjuntor, gw.rag.disjuntor, gw.vetorial.disjuntor)
    return TestClient(gw.app)


def cabecalho(usuario="demo"):
    return {"Authorization": f"Bearer {auth.emitir(usuario)}"}


@pytest.mark.parametrize("metodo,rota", [
    ("post", "/predict"), ("post", "/documentos"), ("post", "/perguntar"),
    ("delete", "/meus-dados"), ("get", "/status"), ("get", "/documentos/lotes/abc")])
def test_rotas_protegidas_exigem_token(cli, metodo, rota):
    assert getattr(cli, metodo)(rota).status_code == 401


def test_login(cli):
    ok = cli.post("/login", json={"usuario": "demo", "senha": "demo-local"})
    assert ok.status_code == 200
    token = ok.json()["access_token"]
    assert cli.get("/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert cli.post("/login", json={"usuario": "demo", "senha": "x"}).status_code == 401
    assert cli.post("/login", json={"usuario": "ninguem", "senha": "x"}).status_code == 401


@pytest.mark.parametrize("token", [
    jwt.encode({"sub": "demo", "iss": auth.EMISSOR, "exp": int(time.time()) - 5},
               auth.segredo_jwt(), algorithm="HS256"),                      # expirado
    jwt.encode({"sub": "demo", "iss": auth.EMISSOR, "exp": int(time.time()) + 60},
               "outro-segredo-qualquer-com-32-bytes!!", algorithm="HS256"),  # assinatura
    jwt.encode({"sub": "demo", "iss": auth.EMISSOR, "exp": int(time.time()) + 60},
               None, algorithm="none"),                                     # sem assinatura
    jwt.encode({"sub": "demo", "iss": auth.EMISSOR},
               auth.segredo_jwt(), algorithm="HS256"),                      # sem exp
    "lixo",
])
def test_tokens_invalidos(cli, token):
    assert cli.get("/status", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_rate_limit_por_usuario(cli, monkeypatch):
    monkeypatch.setattr(gw, "LIMITE_POR_MINUTO", 3)
    monkeypatch.setattr(gw, "limitador", Limitador(gw.banco, relogio=lambda: 1000.0))
    monkeypatch.setenv("USUARIOS", "demo:demo-local,bia:senha-da-bia")
    codigos = [cli.get("/status", headers=cabecalho()).status_code for _ in range(4)]
    assert codigos == [200, 200, 200, 429]
    assert "Retry-After" in cli.get("/status", headers=cabecalho()).headers
    assert cli.get("/status", headers=cabecalho("bia")).status_code == 200


def test_rate_limit_continua_sem_redis():
    class Quebrado:
        def pipeline(self, **kwargs):
            raise redis.ConnectionError()

    limitador = Limitador(Quebrado(), relogio=lambda: 1000.0)
    assert [limitador.verificar("a", 2) > 0 for _ in range(3)] == [False, False, True]


def test_dependencia_fora_abre_circuito_e_responde_503(cli, monkeypatch):
    def cai(*args, **kwargs):
        raise requests.ConnectionError()

    monkeypatch.setattr(gw.inferencia, "_uma_vez", cai)
    monkeypatch.setattr(gw.inferencia, "base_espera", 0)
    for _ in range(gw.inferencia.disjuntor.limite_falhas):
        r = cli.post("/predict", json={"texto": "a"}, headers=cabecalho())
        assert r.status_code == 503
    r = cli.post("/predict", json={"texto": "a"}, headers=cabecalho())
    assert r.status_code == 503 and "circuito aberto" in r.json()["detail"]
    assert "Retry-After" in r.headers
    assert gw.rag.disjuntor.estado == "fechado"  # circuitos independentes


def test_timeout_vira_504(cli, monkeypatch):
    def lento(*args, **kwargs):
        raise requests.Timeout()

    monkeypatch.setattr(gw.rag, "_uma_vez", lento)
    r = cli.post("/perguntar", json={"pergunta": "a"}, headers=cabecalho())
    assert r.status_code == 504


def test_documento_vai_para_a_fila_e_lote_e_privado(cli, r, monkeypatch):
    monkeypatch.setenv("USUARIOS", "demo:demo-local,bia:senha-da-bia")
    resposta = cli.post("/documentos", json={"textos": ["um texto"]}, headers=cabecalho())
    assert resposta.status_code == 202
    lote = resposta.json()["lote"]
    assert r.xlen(fila.STREAM) == 1
    assert r.ttl(fila.chave_lote(lote)) > 0
    url = f"/documentos/lotes/{lote}"
    assert cli.get(url, headers=cabecalho()).json()["estado"] == "pendente"
    assert cli.get(url, headers=cabecalho("bia")).status_code == 404


def test_fila_indisponivel_responde_503(cli, monkeypatch):
    def cai(*args, **kwargs):
        raise redis.ConnectionError()

    monkeypatch.setattr(gw.fila, "publicar", cai)
    r = cli.post("/documentos", json={"textos": ["a"]}, headers=cabecalho())
    assert r.status_code == 503


def test_apagar_meus_dados_limpa_fila_so_do_usuario(cli, r, monkeypatch):
    fila.publicar(r, "demo", ["meu"])
    outro = fila.publicar(r, "bia", ["dela"])
    monkeypatch.setattr(gw.vetorial, "_uma_vez", lambda *a, **k: {"apagados": 0})
    corpo = cli.delete("/meus-dados", headers=cabecalho()).json()
    assert corpo["mensagens_apagadas"] == 1 and corpo["lotes_apagados"] == 1
    assert [c["usuario"] for _, c in r.xrange(fila.STREAM)] == ["bia"]
    assert fila.status(r, outro)


@pytest.mark.parametrize("variavel,valor", [
    ("TOKEN_INTERNO", ""), ("TOKEN_INTERNO", "dev-token-interno"),
    ("JWT_SEGREDO", "curto"), ("USUARIOS", ""), ("USUARIOS", "demo:demo-local")])
def test_producao_recusa_configuracao_fraca(monkeypatch, variavel, valor):
    from comum.interno import token_interno
    monkeypatch.setenv("AMBIENTE", "producao")
    monkeypatch.setenv("TOKEN_INTERNO", "t" * 40)
    monkeypatch.setenv("JWT_SEGREDO", "j" * 40)
    monkeypatch.setenv("USUARIOS", "ana:uma-senha-bem-grande")
    token_interno(), auth.segredo_jwt(), auth.usuarios()  # configuracao boa passa
    monkeypatch.setenv(variavel, valor)
    with pytest.raises(RuntimeError):
        token_interno(), auth.segredo_jwt(), auth.usuarios()
