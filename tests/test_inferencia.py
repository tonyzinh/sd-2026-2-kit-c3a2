import pytest
import requests
from fastapi.testclient import TestClient

from inferencia import app as inf
from tests.conftest import zerar

TOKEN = {"X-Token-Interno": "dev-token-interno"}
FONTE = {"id": "a", "texto": "raft elege um lider", "score": 0.9}


@pytest.fixture
def cli(monkeypatch):
    zerar(inf.disjuntor_llm, inf.vetorial.disjuntor)
    monkeypatch.setattr(inf.vetorial, "_uma_vez", lambda *a, **k: {"resultados": [FONTE]})
    monkeypatch.setattr(inf, "com_retentativas", lambda f, *a: f(*a))  # sem esperas
    return TestClient(inf.app)


def perguntar(cli):
    return cli.post("/perguntar", headers=TOKEN,
                    json={"usuario": "ana", "pergunta": "quem elege o lider?"})


def test_rotas_exigem_token_interno(cli):
    assert cli.post("/predict", json={"texto": "a"}).status_code == 401
    assert cli.post("/predict", json={"texto": "otimo"}, headers=TOKEN).status_code == 200


def test_llm_fora_degrada_e_abre_o_circuito(cli, monkeypatch):
    monkeypatch.setenv("LLM_SIMULAR_FALHA", "1")
    for _ in range(inf.disjuntor_llm.limite_falhas + 1):
        r = perguntar(cli)
        assert r.status_code == 200 and r.json()["degradado"] is True
        assert r.json()["fontes"] == [FONTE]
    assert inf.disjuntor_llm.estado == "aberto"
    assert cli.get("/saude").json()["disjuntores"]["llm"] == "aberto"


def test_vetorial_fora_responde_503(cli, monkeypatch):
    def cai(*args, **kwargs):
        raise requests.ConnectionError()

    monkeypatch.setattr(inf.vetorial, "_uma_vez", cai)
    monkeypatch.setattr(inf.vetorial, "base_espera", 0)
    assert perguntar(cli).status_code == 503
