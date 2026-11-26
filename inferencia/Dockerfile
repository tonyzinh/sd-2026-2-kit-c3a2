# Contexto de build: a raiz do repositorio (precisa do pacote comum/)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY comum/requirements.txt comum/requirements.txt
COPY inferencia/requirements.txt inferencia/requirements.txt
RUN pip install --no-cache-dir -r inferencia/requirements.txt

COPY comum/ comum/
COPY inferencia/ inferencia/
# treina o modelo no build: a subida fica rapida e nada precisa ser gravado em runtime
RUN python -c "from inferencia.modelo import carregar_modelo; carregar_modelo()"

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8001
# PORT e definida pela nuvem; localmente vale a porta padrao.
# --no-access-log: o log padrao do uvicorn grava o IP do cliente (dado pessoal).
CMD ["sh", "-c", "exec uvicorn inferencia.app:app --host 0.0.0.0 --port ${PORT:-8001} --no-access-log"]
