"""
Rastreamento distribuido (OpenTelemetry).

HTTP: a instrumentacao de FastAPI e de requests propaga o cabecalho
`traceparent` (W3C Trace Context) sozinha.
Fila: nao ha cabecalho, entao o contexto e gravado em campos da propria
mensagem (contexto_atual) e restaurado pelo worker (extrair_contexto).

Exportacao, controlada por ambiente:
  OTEL_EXPORTER_OTLP_ENDPOINT  -> envia por OTLP/HTTP (Jaeger local ou nuvem)
  OTEL_CONSOLE=1               -> imprime uma linha JSON por span no log
  nenhum dos dois              -> gera trace_id (aparece nos logs), sem exportar
"""
import json
import os

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (BatchSpanProcessor, ConsoleSpanExporter,
                                            SimpleSpanProcessor)

_configurado = False

# atributos de span que carregam o IP do cliente (dado pessoal)
_ATRIBUTOS_IP = ("net.peer.ip", "client.address", "net.sock.peer.addr",
                 "network.peer.address")


def _linha(span):
    ctx, pai = span.context, span.parent
    return json.dumps({
        "span": span.name, "servico": span.resource.attributes.get("service.name"),
        "trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x"),
        "pai": format(pai.span_id, "016x") if pai else None,
        "ms": round((span.end_time - span.start_time) / 1e6, 2),
    }) + "\n"


def configurar_telemetria(servico):
    """Registra o provedor de rastreamento do processo (uma unica vez)."""
    global _configurado
    if _configurado:
        return
    _configurado = True
    provedor = TracerProvider(resource=Resource.create({"service.name": servico}))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if os.getenv("OTEL_CONSOLE") == "1":
        provedor.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(formatter=_linha)))
    trace.set_tracer_provider(provedor)
    # os GET /saude (healthcheck e "despertador" do worker) nao geram spans
    RequestsInstrumentor().instrument(excluded_urls="saude")


def _ocultar_ip(span, escopo):
    if span and span.is_recording():
        for atributo in _ATRIBUTOS_IP:
            span.set_attribute(atributo, "oculto")


def instrumentar_app(app):
    FastAPIInstrumentor.instrument_app(app, excluded_urls="saude",
                                       server_request_hook=_ocultar_ip,
                                       exclude_spans=["receive", "send"])


def contexto_atual():
    """Contexto de rastreamento atual, pronto para ir como campos da mensagem."""
    portador = {}
    inject(portador)
    return portador


def extrair_contexto(campos):
    return extract({k: campos[k] for k in ("traceparent", "tracestate") if k in campos})
