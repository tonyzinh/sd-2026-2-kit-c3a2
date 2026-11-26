"""
Cliente HTTP resiliente: disjuntor( retentativas( requisicao com tempo-limite ) ).

  tempo-limite   -> nenhuma chamada espera para sempre
  retentativa    -> backoff exponencial com jitter, so para erro transitorio
  disjuntor      -> um por dependencia; conta UMA falha quando as retentativas
                    se esgotam, e ignora 4xx (erro de quem chamou)
"""
import os

import requests

from comum.circuit_breaker import CircuitBreaker
from comum.interno import CABECALHO, token_interno
from comum.retry import com_retentativas, e_transitorio


class ClienteResiliente:

    def __init__(self, nome, url_base, timeout=None, tentativas=None, base_espera=None):
        self.nome = nome
        self.url_base = url_base
        self.timeout = float(timeout or os.getenv("HTTP_TIMEOUT", "5"))
        self.tentativas = int(tentativas or os.getenv("HTTP_TENTATIVAS", "3"))
        self.base_espera = float(base_espera if base_espera is not None
                                 else os.getenv("HTTP_ESPERA_BASE", "0.3"))
        self.disjuntor = CircuitBreaker(
            limite_falhas=int(os.getenv("BREAKER_LIMITE_FALHAS", "3")),
            espera_segundos=float(os.getenv("BREAKER_ESPERA", "20")),
            nome=nome, conta_falha=e_transitorio)

    def _uma_vez(self, metodo, caminho, **kwargs):
        resposta = requests.request(metodo, self.url_base + caminho,
                                    headers={CABECALHO: token_interno()},
                                    timeout=self.timeout, **kwargs)
        resposta.raise_for_status()
        return resposta.json()

    def chamar(self, metodo, caminho, **kwargs):
        """So use tentativas > 1 em operacoes idempotentes."""
        return self.disjuntor.chamar(
            com_retentativas, self._uma_vez, metodo, caminho,
            tentativas=self.tentativas, base=self.base_espera, **kwargs)
