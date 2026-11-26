"""
Sobe a plataforma SEM Docker (Redis falso + 4 processos). So para desenvolvimento;
a forma oficial de executar e `docker compose up --build`.

  pip install -r requirements-dev.txt
  python scripts/rodar_local.py            (Ctrl+C encerra)
  python scripts/rodar_local.py --testar   (sobe, roda o smoke test e encerra)

Logs em dados/logs/.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fakeredis import TcpFakeServer

RAIZ = Path(__file__).resolve().parents[1]
LOGS = RAIZ / "dados" / "logs"
PORTA_REDIS = 6390
SERVICOS = {"vetorial": 8002, "inferencia": 8001, "worker": 8003, "gateway": 8000}


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    redis_falso = TcpFakeServer(("127.0.0.1", PORTA_REDIS), server_type="redis")
    redis_falso.daemon_threads = True
    threading.Thread(target=redis_falso.serve_forever, daemon=True).start()

    ambiente = {**os.environ, "REDIS_URL": f"redis://127.0.0.1:{PORTA_REDIS}/0",
                "OTEL_CONSOLE": "1", "PYTHONUNBUFFERED": "1",
                "URL_INFERENCIA": "http://127.0.0.1:8001",
                "URL_VETORIAL": "http://127.0.0.1:8002"}
    processos, arquivos = [], []
    try:
        for nome, porta in SERVICOS.items():
            saida = open(LOGS / f"{nome}.log", "w", encoding="utf-8")
            arquivos.append(saida)
            processos.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", f"{nome}.app:app", "--port", str(porta),
                 "--no-access-log"],
                cwd=RAIZ, env=ambiente, stdout=saida, stderr=subprocess.STDOUT))
        if "--testar" in sys.argv:
            codigo = subprocess.call(
                [sys.executable, str(RAIZ / "scripts" / "smoke.py"), "http://127.0.0.1:8000"],
                env={**ambiente, "SMOKE_PRAZO": "90"})
            time.sleep(2)  # deixa os ultimos spans chegarem ao log
            return codigo
        print("gateway em http://localhost:8000/docs (demo / demo-local). Ctrl+C encerra.")
        while all(p.poll() is None for p in processos):
            time.sleep(1)
        print(f"um servico encerrou; veja {LOGS}")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in processos:
            p.terminate()
        for p in processos:
            p.wait(10)
        for a in arquivos:
            a.close()


if __name__ == "__main__":
    sys.exit(main())
