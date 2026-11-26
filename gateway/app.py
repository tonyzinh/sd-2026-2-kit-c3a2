"""
API Gateway - porta de entrada unica da plataforma.

Responsabilidades: autenticacao JWT, rate limit, validacao, roteamento
resiliente (REST) e publicacao na fila. Nao tem logica de IA.

Rodar: uvicorn gateway.app:app --port 8000
"""
import math
import os
import threading
import time

import redis
import requests
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from comum import fila
from comum.circuit_breaker import CircuitoAberto
from comum.cliente import ClienteResiliente
from comum.config import producao, retencao_segundos, url_servico
from comum.interno import token_interno
from comum.logs import configurar_logs
from comum.telemetria import configurar_telemetria, contexto_atual, instrumentar_app
from gateway import auth
from gateway.limite import Limitador

configurar_telemetria("gateway")
log = configurar_logs("gateway")
tracer = trace.get_tracer("gateway")
# falha na subida (e nao na primeira requisicao) se a configuracao for insegura
token_interno()
auth.segredo_jwt()
auth.usuarios()

LIMITE_POR_MINUTO = int(os.getenv("LIMITE_POR_MINUTO", "60"))
LIMITE_LOGIN_IP = int(os.getenv("LIMITE_LOGIN_IP", "10"))
LIMITE_LOGIN_GLOBAL = int(os.getenv("LIMITE_LOGIN_GLOBAL", "60"))

URL_INFERENCIA = url_servico("URL_INFERENCIA", "http://localhost:8001")
URL_VETORIAL = url_servico("URL_VETORIAL", "http://localhost:8002")
URL_WORKER = url_servico("URL_WORKER", "")

banco = fila.conectar()
limitador = Limitador(banco)
# um disjuntor por rota de dependencia: o RAG fora do ar nao derruba o /predict
inferencia = ClienteResiliente("inferencia", URL_INFERENCIA)
# sem retentativa aqui: a inferencia ja repete as chamadas dela (evita 3 x 3)
rag = ClienteResiliente("rag", URL_INFERENCIA, tentativas=1,
                        timeout=os.getenv("TIMEOUT_PERGUNTAR", "30"))
vetorial = ClienteResiliente("vetorial", URL_VETORIAL)

app = FastAPI(title="Plataforma de IA - API Gateway", version="1.0.0",
              description="Faca POST /login, copie o token e use o botao Authorize.")
instrumentar_app(app)

Documento = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                             max_length=5000)]


class Credenciais(BaseModel):
    usuario: str = Field(max_length=64)
    senha: str = Field(max_length=128)


class Entrada(BaseModel):
    texto: str = Field(min_length=1, max_length=5000)


class LoteDocumentos(BaseModel):
    textos: list[Documento] = Field(min_length=1, max_length=20)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(3, ge=1, le=10)


@app.middleware("http")
async def registrar(request: Request, call_next):
    inicio = time.perf_counter()
    resposta = await call_next(request)
    ms = (time.perf_counter() - inicio) * 1000
    if producao():  # manda o navegador usar somente HTTPS
        resposta.headers["Strict-Transport-Security"] = "max-age=31536000"
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        resposta.headers["X-Trace-Id"] = format(ctx.trace_id, "032x")
    if request.url.path != "/saude":
        # o molde da rota (e nao o caminho) para nao registrar identificadores
        rota = getattr(request.scope.get("route"), "path", "desconhecida")
        log.info("%s %s -> %d (%.1fms)", request.method, rota, resposta.status_code, ms)
    return resposta


@app.exception_handler(redis.RedisError)
def redis_fora(request: Request, exc: redis.RedisError):
    log.error("redis indisponivel: %s", type(exc).__name__)
    return JSONResponse({"detail": "fila indisponivel; tente novamente"}, status_code=503)


def _limitar(identidade, limite):
    espera = limitador.verificar(identidade, limite)
    if espera:
        raise HTTPException(429, "limite de requisicoes excedido",
                            headers={"Retry-After": str(espera)})


def usuario_limitado(usuario: str = Depends(auth.usuario_atual)):
    _limitar(f"usuario:{usuario}", LIMITE_POR_MINUTO)
    return usuario


def _encaminhar(cliente, metodo, caminho, **kwargs):
    try:
        return cliente.chamar(metodo, caminho, **kwargs)
    except CircuitoAberto as e:
        raise HTTPException(503, f"servico {cliente.nome} indisponivel (circuito aberto)",
                            headers={"Retry-After": str(max(1, math.ceil(e.retry_apos)))})
    except requests.Timeout:
        raise HTTPException(504, f"tempo esgotado no servico {cliente.nome}")
    except requests.RequestException as e:
        log.error("falha em %s: %s", cliente.nome, type(e).__name__)
        raise HTTPException(503, f"servico {cliente.nome} indisponivel")


def _acordar_worker():
    """No free tier o worker hiberna sem trafego HTTP; um GET o acorda."""
    try:
        requests.get(f"{URL_WORKER}/saude", timeout=90)
    except requests.RequestException as e:
        log.warning("nao foi possivel acordar o worker: %s", type(e).__name__)


def acordar_em_segundo_plano():
    if URL_WORKER:
        threading.Thread(target=_acordar_worker, daemon=True).start()


@app.get("/")
def raiz():
    return {"servico": "plataforma de IA", "documentacao": "/docs", "saude": "/saude"}


@app.get("/saude")
def saude():
    return {"status": "ok"}


@app.post("/login")
def login(c: Credenciais, request: Request):
    ip = request.client.host if request.client else "?"
    _limitar(f"login-ip:{ip}", LIMITE_LOGIN_IP)
    # teto global: vale mesmo que alguem forje o X-Forwarded-For
    _limitar("login-global", LIMITE_LOGIN_GLOBAL)
    if not auth.autenticar(c.usuario, c.senha):
        raise HTTPException(401, "usuario ou senha invalidos")
    return {"access_token": auth.emitir(c.usuario), "token_type": "bearer",
            "expira_em_segundos": auth.EXPIRA_MINUTOS * 60}


@app.post("/predict")
def predict(entrada: Entrada, usuario: str = Depends(usuario_limitado)):
    return _encaminhar(inferencia, "POST", "/predict", json=entrada.model_dump())


@app.post("/documentos", status_code=202)
def enviar_documentos(lote: LoteDocumentos, tarefas: BackgroundTasks,
                      usuario: str = Depends(usuario_limitado)):
    with tracer.start_as_current_span("fila publicar", kind=SpanKind.PRODUCER) as span:
        ident = fila.publicar(banco, usuario, lote.textos, contexto_atual())
        span.set_attribute("lote.id", ident)
    tarefas.add_task(acordar_em_segundo_plano)
    return {"lote": ident, "estado": "pendente", "status_url": f"/documentos/lotes/{ident}"}


@app.get("/documentos/lotes/{lote}")
def status_do_lote(lote: str, tarefas: BackgroundTasks,
                   usuario: str = Depends(usuario_limitado)):
    dados = fila.status(banco, lote)
    # 404 tambem para lote de outro usuario: nao revela que ele existe
    if dados is None or dados.pop("usuario", None) != usuario:
        raise HTTPException(404, "lote desconhecido ou expirado")
    if dados.get("estado") == "pendente":
        tarefas.add_task(acordar_em_segundo_plano)
    return {"lote": lote, **dados}


@app.post("/perguntar")
def perguntar(p: Pergunta, usuario: str = Depends(usuario_limitado)):
    return _encaminhar(rag, "POST", "/perguntar",
                       json={"usuario": usuario, **p.model_dump()})


@app.delete("/meus-dados")
def apagar_meus_dados(usuario: str = Depends(usuario_limitado)):
    """LGPD: elimina os documentos, as mensagens pendentes e os status do usuario."""
    # o vetorial primeiro: a marca de eliminacao barra o que o worker tiver em maos
    apagados = _encaminhar(vetorial, "DELETE", f"/usuarios/{usuario}")["apagados"]
    mensagens = fila.apagar_do_usuario(banco, usuario)
    lotes = 0
    for chave in banco.scan_iter(fila.chave_lote("*")):
        if banco.hget(chave, "usuario") == usuario:
            lotes += banco.delete(chave)
    return {"documentos_apagados": apagados, "mensagens_apagadas": mensagens,
            "lotes_apagados": lotes, "retencao_padrao_horas": retencao_segundos() / 3600}


@app.get("/status")
def status_da_plataforma(usuario: str = Depends(usuario_limitado)):
    """Estado dos disjuntores do gateway e metricas da fila."""
    try:
        metricas = fila.metricas(banco)
    except redis.RedisError:
        metricas = "indisponivel"
    return {"disjuntores": {c.nome: c.disjuntor.status()
                            for c in (inferencia, rag, vetorial)},
            "fila": metricas}
