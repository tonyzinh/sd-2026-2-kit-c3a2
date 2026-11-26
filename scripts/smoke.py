"""
Teste de ponta a ponta, somente pelo gateway. Usa so a biblioteca padrao.

  python scripts/smoke.py                                   (local: demo / demo-local)
  python scripts/smoke.py https://SEU-GATEWAY.onrender.com  (defina SMOKE_USUARIO e SMOKE_SENHA)

ATENCAO: o ultimo passo apaga os dados do usuario usado no teste.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
USUARIO = os.getenv("SMOKE_USUARIO", "demo")
SENHA = os.getenv("SMOKE_SENHA", "demo-local")
PRAZO = float(os.getenv("SMOKE_PRAZO", "240"))  # cobre o despertar no free tier


def chamar(metodo, caminho, corpo=None, token=None):
    cabecalhos = {"Content-Type": "application/json"}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    pedido = urllib.request.Request(
        BASE + caminho, method=metodo, headers=cabecalhos,
        data=json.dumps(corpo).encode() if corpo is not None else None)
    try:
        with urllib.request.urlopen(pedido, timeout=120) as r:
            return r.status, json.loads(r.read()), r.headers
    except urllib.error.HTTPError as e:
        bruto = e.read()
        try:
            return e.code, json.loads(bruto), e.headers
        except ValueError:
            return e.code, {"detail": bruto[:200].decode(errors="replace")}, e.headers
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return 0, {"detail": str(e)}, {}


def insistir(descricao, funcao, aceito):
    """Repete ate aceito(resposta) ou ate o prazo (servicos podem estar acordando)."""
    fim = time.monotonic() + PRAZO
    while True:
        resposta = funcao()
        if aceito(resposta):
            return resposta
        if time.monotonic() > fim:
            falhar(f"{descricao}: HTTP {resposta[0]} {resposta[1]}")
        time.sleep(3)


def falhar(mensagem):
    print(f"FALHOU  {mensagem}")
    sys.exit(1)


def ok(mensagem):
    print(f"ok      {mensagem}", flush=True)


def main():
    insistir("saude do gateway", lambda: chamar("GET", "/saude"), lambda r: r[0] == 200)
    ok("gateway no ar")

    status, _, _ = chamar("POST", "/predict", {"texto": "teste"})
    if status != 401:
        falhar(f"/predict sem token deveria dar 401, deu {status}")
    ok("sem token: 401")

    status, corpo, _ = chamar("POST", "/login", {"usuario": USUARIO, "senha": SENHA})
    if status != 200:
        falhar(f"login: HTTP {status} {corpo}")
    token = corpo["access_token"]
    ok("login emitiu JWT")

    _, corpo, _ = insistir(
        "predict", lambda: chamar("POST", "/predict", {"texto": "servico excelente"}, token),
        lambda r: r[0] == 200)
    ok(f"predict (gateway -> inferencia): {corpo['sentimento']} {corpo['confianca']}")

    marca = uuid.uuid4().hex[:8]
    texto = f"O teorema CAP trata de particoes de rede. Marcador {marca}."
    status, corpo, cabecalhos = chamar("POST", "/documentos", {"textos": [texto]}, token)
    if status != 202:
        falhar(f"documentos: HTTP {status} {corpo}")
    lote, trace_id = corpo["lote"], cabecalhos.get("X-Trace-Id", "-")
    ok(f"documento enfileirado (202), trace_id={trace_id}")

    _, corpo, _ = insistir(
        "processamento do lote",
        lambda: chamar("GET", f"/documentos/lotes/{lote}", token=token),
        lambda r: r[0] == 200 and r[1]["estado"] in ("concluido", "falhou"))
    if corpo["estado"] != "concluido":
        falhar(f"lote terminou como {corpo}")
    ok("worker processou o lote")

    _, corpo, _ = insistir(
        "perguntar",
        lambda: chamar("POST", "/perguntar", {"pergunta": f"o que diz o teorema CAP? {marca}"}, token),
        lambda r: r[0] == 200)
    if not any(marca in f["texto"] for f in corpo["fontes"]):
        falhar(f"a busca vetorial nao devolveu o documento enviado: {corpo}")
    ok(f"RAG (inferencia -> vetorial -> LLM), degradado={corpo['degradado']}")

    status, corpo, _ = chamar("DELETE", "/meus-dados", token=token)
    if status != 200 or corpo["documentos_apagados"] < 1:
        falhar(f"eliminacao de dados: HTTP {status} {corpo}")
    ok(f"LGPD: {corpo['documentos_apagados']} documento(s) eliminado(s)")
    print("\nPLATAFORMA OK")


if __name__ == "__main__":
    main()
