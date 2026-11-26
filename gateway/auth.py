"""
Autenticacao por JWT (HS256).

Usuarios vem do ambiente: USUARIOS="ana:senha1,bia:senha2". Nao ha cadastro.
"""
import hmac
import os
import re
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from comum.config import producao, segredo

EMISSOR = "plataforma-ia"
EXPIRA_MINUTOS = int(os.getenv("JWT_EXPIRA_MINUTOS", "60"))
_NOME_VALIDO = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_portador = HTTPBearer(auto_error=False)


def segredo_jwt():
    return segredo("JWT_SEGREDO", "dev-segredo-jwt-apenas-para-uso-local", minimo=32)


def usuarios():
    bruto = os.getenv("USUARIOS") or ""
    if not bruto:
        if producao():
            raise RuntimeError("defina USUARIOS (formato usuario:senha) em producao")
        bruto = "demo:demo-local"
    resultado = {}
    for par in bruto.split(","):
        nome, separador, senha = par.strip().partition(":")
        if not separador or not senha or not _NOME_VALIDO.match(nome):
            raise RuntimeError("USUARIOS invalido: use usuario:senha,usuario2:senha2")
        if producao() and (len(senha) < 12 or senha == "demo-local"):
            raise RuntimeError("em producao cada senha precisa de 12+ caracteres")
        resultado[nome] = senha
    return resultado


def autenticar(usuario, senha):
    esperada = usuarios().get(usuario)
    # compara sempre, mesmo sem usuario, para nao revelar quem existe pelo tempo
    confere = hmac.compare_digest(senha.encode(), (esperada or "\0").encode())
    return confere and esperada is not None


def emitir(usuario):
    agora = int(time.time())
    return jwt.encode({"sub": usuario, "iss": EMISSOR, "iat": agora,
                       "exp": agora + EXPIRA_MINUTOS * 60},
                      segredo_jwt(), algorithm="HS256")


def usuario_atual(cred: HTTPAuthorizationCredentials = Depends(_portador)):
    negado = HTTPException(401, "token ausente, invalido ou expirado",
                           headers={"WWW-Authenticate": "Bearer"})
    if cred is None:
        raise negado
    try:
        dados = jwt.decode(cred.credentials, segredo_jwt(), algorithms=["HS256"],
                           issuer=EMISSOR, options={"require": ["sub", "exp", "iss"]})
    except jwt.InvalidTokenError:
        raise negado
    if dados["sub"] not in usuarios():  # usuario removido perde o acesso
        raise negado
    return dados["sub"]
