"""
Logs com o trace_id do OpenTelemetry.

Regra de LGPD do projeto: NUNCA registrar texto enviado pelo usuario, nome de
usuario, senha, token ou endereco IP.
"""
import logging
import os

from opentelemetry import trace


class _FiltroTrace(logging.Filter):
    def filter(self, record):
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        return True


def configurar_logs(servico):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        f"%(asctime)s %(levelname)s [{servico}] [%(name)s] [trace=%(trace_id)s] %(message)s"))
    handler.addFilter(_FiltroTrace())
    # "retry" e "disjuntor" registram retentativas e transicoes no mesmo formato
    for nome in ("plataforma", "retry", "disjuntor"):
        logger = logging.getLogger(nome)
        logger.handlers[:] = [handler]
        logger.propagate = False
        logger.setLevel(os.getenv("LOG_NIVEL", "INFO"))
    return logging.getLogger(f"plataforma.{servico}")
