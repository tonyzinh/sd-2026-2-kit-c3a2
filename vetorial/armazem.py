"""
Armazenamento vetorial sobre Redis.

  vet:<usuario>:<id>     documento + vetor, com TTL igual a retencao
  vet:idx:<usuario>      conjunto ordenado id -> instante de expiracao
  vet:apagado:<usuario>  instante da ultima eliminacao pedida pelo usuario

Busca exata (varre os vetores do usuario e calcula o cosseno). E O(n), o que
basta para o volume de um free tier; ver o relatorio de trade-offs no README.
"""
import json
import math
import time

import redis

from comum.config import retencao_segundos


def cosseno(a, b):
    produto = sum(x * y for x, y in zip(a, b))
    norma = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return produto / norma if norma else 0.0


def _doc(usuario, ident):
    return f"vet:{usuario}:{ident}"


def _indice(usuario):
    return f"vet:idx:{usuario}"


def _marca(usuario):
    return f"vet:apagado:{usuario}"


def _ids_validos(r, usuario):
    r.zremrangebyscore(_indice(usuario), "-inf", time.time())
    return r.zrange(_indice(usuario), 0, -1)


def total(r, usuario):
    return len(_ids_validos(r, usuario))


def gravar(r, usuario, itens, criado_em):
    """Upsert idempotente. Ignora trabalho criado antes de uma eliminacao."""
    ttl = retencao_segundos()
    with r.pipeline(transaction=True) as p:
        while True:
            try:
                # WATCH: se uma eliminacao chegar entre a leitura da marca e a
                # escrita, a transacao e abortada e a checagem e refeita
                p.watch(_marca(usuario))
                if criado_em <= float(p.get(_marca(usuario)) or 0):
                    return 0
                expira = time.time() + ttl
                p.multi()
                for item in itens:
                    p.set(_doc(usuario, item["id"]),
                          json.dumps({"texto": item["texto"], "vetor": item["vetor"]}), ex=ttl)
                    p.zadd(_indice(usuario), {item["id"]: expira})
                p.expire(_indice(usuario), ttl)
                p.execute()
                return len(itens)
            except redis.WatchError:
                continue


def buscar(r, usuario, vetor, top_k):
    ids = _ids_validos(r, usuario)
    if not ids:
        return []
    pontuados = []
    for ident, bruto in zip(ids, r.mget([_doc(usuario, i) for i in ids])):
        if bruto is None:
            continue
        item = json.loads(bruto)
        score = cosseno(vetor, item["vetor"])
        if score > 0:
            pontuados.append({"id": ident, "texto": item["texto"], "score": round(score, 4)})
    pontuados.sort(key=lambda p: p["score"], reverse=True)
    return pontuados[:top_k]


def apagar(r, usuario):
    r.set(_marca(usuario), repr(time.time()), ex=retencao_segundos())
    ids = r.zrange(_indice(usuario), 0, -1)
    if ids:
        r.delete(*[_doc(usuario, i) for i in ids])
    r.delete(_indice(usuario))
    return len(ids)
