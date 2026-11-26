# Contexto de build: a raiz do repositorio (precisa do pacote comum/)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY comum/requirements.txt comum/requirements.txt
COPY gateway/requirements.txt gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt

COPY comum/ comum/
COPY gateway/ gateway/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
# PORT e definida pela nuvem; localmente vale a porta padrao.
# --no-access-log: o log padrao do uvicorn grava o IP do cliente (dado pessoal).
CMD ["sh", "-c", "exec uvicorn gateway.app:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
