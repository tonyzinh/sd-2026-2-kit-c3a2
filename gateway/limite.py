"""
Rate limit por janela fixa de 1 minuto.

O contador fica no Redis para valer em todas as replicas do gateway. Se o Redis
cair, usa um contador em memoria: o limite passa a ser por replica, mas o
gateway continua protegido e no ar.
"""
import hashlib
import logging
import threading
import time

import redis

log = logging.getLogger("plataforma.limite")


class Limitador:

    def __init__(self, banco, relogio=time.time):
        self.banco = banco
        self._relogio = relogio
        self._memoria = {}
        self._lock = threading.Lock()

    def _contar_local(self, chave, janela):
        with self._lock:
            for antiga in [c for c in self._memoria if c[1] != janela]:
                del self._memoria[antiga]
            self._memoria[(chave, janela)] = self._memoria.get((chave, janela), 0) + 1
            return self._memoria[(chave, janela)]

    def verificar(self, identidade, limite):
        """Devolve 0 se pode passar, ou os segundos ate a proxima janela."""
        agora = self._relogio()
        janela = int(agora // 60)
        # o identificador (usuario ou IP) vai com hash: nada em claro no Redis
        chave = "limite:" + hashlib.sha256(identidade.encode()).hexdigest()[:24]
        try:
            with self.banco.pipeline(transaction=True) as p:
                p.incr(f"{chave}:{janela}")
                p.expire(f"{chave}:{janela}", 70)
                contagem = p.execute()[0]
        except redis.RedisError:
            log.warning("redis indisponivel: rate limit em memoria")
            contagem = self._contar_local(chave, janela)
        return 0 if contagem <= limite else max(1, int(60 - agora % 60))
