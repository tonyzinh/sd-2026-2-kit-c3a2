"""
Worker de fila. O consumidor roda em uma thread; o HTTP existe so para o
healthcheck (e porque o free tier da nuvem nao tem "background worker").

Rodar: uvicorn worker.app:app --port 8003
"""
import os
import socket
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from comum.cliente import ClienteResiliente
from comum.config import url_servico
from comum.interno import token_interno
from comum.logs import configurar_logs
from comum.telemetria import configurar_telemetria
from worker import consumidor

configurar_telemetria("worker")
log = configurar_logs("worker")
token_interno()

_parar = threading.Event()
_estado = {"batimento": time.monotonic(), "thread": None}
LIMITE_SEM_BATIMENTO = float(os.getenv("WORKER_LIMITE_BATIMENTO", "300"))


def _bater():
    _estado["batimento"] = time.monotonic()


@asynccontextmanager
async def ciclo_de_vida(app):
    inferencia = ClienteResiliente(
        "inferencia", url_servico("URL_INFERENCIA", "http://localhost:8001"))
    vetorial = ClienteResiliente(
        "vetorial", url_servico("URL_VETORIAL", "http://localhost:8002"))
    nome = f"{socket.gethostname()}-{os.getpid()}"
    _parar.clear()
    _estado["thread"] = threading.Thread(
        target=consumidor.executar, args=(inferencia, vetorial, nome, _parar, _bater),
        daemon=True)
    _estado["thread"].start()
    log.info("consumidor iniciado")
    yield
    _parar.set()
    _estado["thread"].join(10)


# sem instrumentar_app: o unico HTTP e o healthcheck; os spans saem do consumidor
app = FastAPI(title="Worker", lifespan=ciclo_de_vida)


@app.get("/saude")
def saude():
    thread = _estado["thread"]
    parado_ha = time.monotonic() - _estado["batimento"]
    vivo = bool(thread and thread.is_alive() and parado_ha < LIMITE_SEM_BATIMENTO)
    return JSONResponse({"status": "ok" if vivo else "parado"},
                        status_code=200 if vivo else 503)
