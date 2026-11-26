"""
Autenticacao entre servicos.

Na nuvem (free tier) inferencia e vetorial tem URL publica, entao toda rota que
nao seja /saude exige o cabecalho X-Token-Interno.
"""
import hmac

from fastapi import Header, HTTPException

from comum.config import segredo

CABECALHO = "X-Token-Interno"


def token_interno():
    return segredo("TOKEN_INTERNO", "dev-token-interno")


def exigir_token_interno(x_token_interno: str = Header(default="")):
    if not hmac.compare_digest(x_token_interno.encode(), token_interno().encode()):
        raise HTTPException(401, "token interno ausente ou invalido")
