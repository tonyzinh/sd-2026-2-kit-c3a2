"""Servico de armazenamento vetorial (interno). Rodar: uvicorn vetorial.app:app --port 8002"""
import redis
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from comum import fila
from comum.interno import exigir_token_interno, token_interno
from comum.logs import configurar_logs
from comum.telemetria import configurar_telemetria, instrumentar_app
from vetorial import armazem

configurar_telemetria("vetorial")
log = configurar_logs("vetorial")
token_interno()  # falha na subida se o segredo estiver fraco em producao

app = FastAPI(title="Armazenamento vetorial")
instrumentar_app(app)
rotas = APIRouter(dependencies=[Depends(exigir_token_interno)])
banco = fila.conectar()

USUARIO = r"^[A-Za-z0-9_.-]{1,64}$"


class Item(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    texto: str = Field(max_length=5000)
    vetor: list[float] = Field(min_length=1, max_length=4096)


class Lote(BaseModel):
    usuario: str = Field(pattern=USUARIO)
    criado_em: float
    itens: list[Item] = Field(max_length=100)


class Busca(BaseModel):
    usuario: str = Field(pattern=USUARIO)
    vetor: list[float] = Field(min_length=1, max_length=4096)
    top_k: int = Field(3, ge=1, le=20)


@app.exception_handler(redis.RedisError)
def redis_fora(request: Request, exc: redis.RedisError):
    log.error("redis indisponivel: %s", type(exc).__name__)
    return JSONResponse({"detail": "armazenamento indisponivel"}, status_code=503)


@rotas.put("/itens")
def gravar(lote: Lote):
    n = armazem.gravar(banco, lote.usuario, [i.model_dump() for i in lote.itens],
                       lote.criado_em)
    log.info("gravados %d itens", n)
    return {"gravados": n, "total": armazem.total(banco, lote.usuario)}


@rotas.post("/buscar")
def buscar(busca: Busca):
    return {"resultados": armazem.buscar(banco, busca.usuario, busca.vetor, busca.top_k)}


@rotas.delete("/usuarios/{usuario}")
def apagar(usuario: str):
    return {"apagados": armazem.apagar(banco, usuario)}


@app.get("/saude")
def saude():
    banco.ping()
    return {"status": "ok"}


app.include_router(rotas)
