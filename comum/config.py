"""Configuracao lida do ambiente, compartilhada pelos servicos."""
import os

SEGREDOS_DE_DESENVOLVIMENTO = {"dev-token-interno", "dev-segredo-jwt-apenas-para-uso-local"}


def producao():
    # o Render define RENDER=true em todos os servicos
    return os.getenv("AMBIENTE") == "producao" or os.getenv("RENDER") == "true"


def segredo(nome, padrao_dev, minimo=16):
    """Segredo obrigatorio. Em producao recusa valor ausente, curto ou de exemplo."""
    valor = os.getenv(nome) or ""
    if producao():
        if len(valor) < minimo or valor in SEGREDOS_DE_DESENVOLVIMENTO:
            raise RuntimeError(f"{nome} ausente ou fraco: defina um valor proprio em producao")
        return valor
    return valor or padrao_dev


def url_servico(nome, padrao):
    """Aceita URL completa ou apenas o hostname (como o render.yaml entrega)."""
    valor = (os.getenv(nome) or padrao).strip().rstrip("/")
    if valor and "://" not in valor:
        valor = f"https://{valor}"
    return valor


def retencao_segundos():
    return int(float(os.getenv("RETENCAO_HORAS", "24")) * 3600)
