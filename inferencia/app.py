"""
Servico de inferencia (interno). A IA e uma caixa-preta:
  POST /predict     sentimento (modelo pronto da disciplina)
  POST /embeddings  vetores dos textos
  POST /perguntar   RAG: embedding -> busca no vetorial -> LLM

Rodar: uvicorn inferencia.app:app --port 8001
"""
import math
import os

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from comum.circuit_breaker import CircuitBreaker, CircuitoAberto
from comum.cliente import ClienteResiliente
from comum.config import url_servico
from comum.interno import exigir_token_interno, token_interno
from comum.logs import configurar_logs
from comum.retry import com_retentativas, e_transitorio
from comum.telemetria import configurar_telemetria, instrumentar_app
from inferencia import llm
from inferencia.embeddings import vetorizar
from inferencia.modelo import carregar_modelo

configurar_telemetria("inferencia")
log = configurar_logs("inferencia")
token_interno()

app = FastAPI(title="Inferencia")
instrumentar_app(app)
rotas = APIRouter(dependencies=[Depends(exigir_token_interno)])

modelo = carregar_modelo()  # uma vez so, na subida
vetorial = ClienteResiliente("vetorial", url_servico("URL_VETORIAL", "http://localhost:8002"))
disjuntor_llm = CircuitBreaker(
    limite_falhas=int(os.getenv("BREAKER_LIMITE_FALHAS", "3")),
    espera_segundos=float(os.getenv("BREAKER_ESPERA", "20")),
    nome="llm", conta_falha=e_transitorio)


class Texto(BaseModel):
    texto: str = Field(min_length=1, max_length=5000)


class Textos(BaseModel):
    textos: list[str] = Field(min_length=1, max_length=100)


class Pergunta(BaseModel):
    usuario: str = Field(max_length=64)
    pergunta: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(3, ge=1, le=10)


def _indisponivel(detalhe, espera=None):
    cabecalhos = {"Retry-After": str(max(1, math.ceil(espera)))} if espera is not None else None
    return HTTPException(503, detalhe, headers=cabecalhos)


@rotas.post("/predict")
def predict(entrada: Texto):
    return modelo.prever(entrada.texto)


@rotas.post("/embeddings")
def embeddings(entrada: Textos):
    return {"vetores": vetorizar(entrada.textos)}


@rotas.post("/perguntar")
def perguntar(p: Pergunta):
    try:
        busca = vetorial.chamar("POST", "/buscar", json={
            "usuario": p.usuario, "vetor": vetorizar([p.pergunta])[0], "top_k": p.top_k})
    except CircuitoAberto as e:
        raise _indisponivel("armazenamento vetorial indisponivel (circuito aberto)", e.retry_apos)
    except requests.RequestException as e:
        log.error("vetorial falhou: %s", type(e).__name__)
        raise _indisponivel("armazenamento vetorial indisponivel")

    contexto = [t["texto"] for t in busca["resultados"]]
    if not contexto:
        return {"resposta": "Nao encontrei documentos relevantes para responder.",
                "fontes": [], "degradado": False}
    try:
        resposta = disjuntor_llm.chamar(com_retentativas, llm.gerar, p.pergunta, contexto)
    except (CircuitoAberto, requests.RequestException) as e:
        # degradacao graciosa: sem LLM, ainda devolve os trechos recuperados
        log.warning("llm indisponivel (%s); resposta degradada", type(e).__name__)
        return {"resposta": "Modelo de linguagem indisponivel no momento. "
                            "Seguem os trechos mais relevantes.",
                "fontes": busca["resultados"], "degradado": True}
    return {"resposta": resposta, "fontes": busca["resultados"], "degradado": False}


@app.get("/saude")
def saude():
    return {"status": "ok",
            "disjuntores": {"llm": disjuntor_llm.status()["estado"],
                            "vetorial": vetorial.disjuntor.status()["estado"]}}


app.include_router(rotas)
