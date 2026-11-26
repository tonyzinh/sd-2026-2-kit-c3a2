import fakeredis
import pytest
import requests

from comum.circuit_breaker import CircuitBreaker


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


def erro_http(status):
    resposta = requests.Response()
    resposta.status_code = status
    return requests.HTTPError(response=resposta)


def zerar(*disjuntores: CircuitBreaker):
    for d in disjuntores:
        d.estado, d.falhas, d._sonda_em_andamento = "fechado", 0, False
