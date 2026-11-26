import pytest
import requests

from comum import cliente as modulo
from comum.circuit_breaker import CircuitoAberto
from tests.conftest import erro_http


class Resposta:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise erro_http(self.status_code)

    def json(self):
        return {"ok": True}


def montar(monkeypatch, respostas, **kwargs):
    """Cliente cujas requisicoes devolvem (ou levantam) os itens de respostas."""
    chamadas = []

    def falsa(metodo, url, **opcoes):
        chamadas.append(opcoes)
        item = respostas[min(len(chamadas) - 1, len(respostas) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(modulo.requests, "request", falsa)
    return modulo.ClienteResiliente("x", "http://x", base_espera=0, **kwargs), chamadas


def test_toda_chamada_tem_tempo_limite_e_token(monkeypatch):
    c, chamadas = montar(monkeypatch, [Resposta()], timeout=2)
    c.chamar("GET", "/a")
    assert chamadas[0]["timeout"] == 2
    assert chamadas[0]["headers"]["X-Token-Interno"]


def test_retenta_erro_transitorio_ate_dar_certo(monkeypatch):
    c, chamadas = montar(monkeypatch, [requests.Timeout(), Resposta(503), Resposta()])
    assert c.chamar("GET", "/a") == {"ok": True}
    assert len(chamadas) == 3


def test_4xx_nao_retenta_nem_abre_o_circuito(monkeypatch):
    c, chamadas = montar(monkeypatch, [Resposta(422)])
    for _ in range(10):
        with pytest.raises(requests.HTTPError):
            c.chamar("GET", "/a")
    assert len(chamadas) == 10
    assert c.disjuntor.estado == "fechado"


def test_circuito_abre_recusa_sem_chamar_e_se_recupera(monkeypatch):
    c, chamadas = montar(monkeypatch, [requests.ConnectionError()], tentativas=2)
    agora = [0.0]
    c.disjuntor._relogio = lambda: agora[0]
    for _ in range(c.disjuntor.limite_falhas):
        with pytest.raises(requests.ConnectionError):
            c.chamar("GET", "/a")
    feitas = len(chamadas)
    assert c.disjuntor.estado == "aberto"
    with pytest.raises(CircuitoAberto):
        c.chamar("GET", "/a")
    assert len(chamadas) == feitas  # aberto: nem tenta

    agora[0] += c.disjuntor.espera_segundos + 1
    monkeypatch.setattr(modulo.requests, "request", lambda *a, **k: Resposta())
    assert c.chamar("GET", "/a") == {"ok": True}  # sonda no estado "meio"
    assert c.disjuntor.estado == "fechado"
