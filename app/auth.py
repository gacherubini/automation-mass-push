"""Autenticacao do dashboard: argon2 + cookie de sessao.

A sessao em si e assinada pelo `SessionMiddleware` do Starlette (itsdangerous
por baixo). Este modulo so decide *o que* vai dentro do cookie e como a senha
e verificada — nao monta middleware nem rota.

Referencia de desenho: portal-gestao do bot-whatsapp-financiamento, mesma
stack, sem papeis (aqui todo usuario logado tem o mesmo poder).
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Usuario

# Parametros default do argon2-cffi ja sao os recomendados atuais (argon2id).
# Uma unica instancia reutilizada: o hasher e thread-safe para hash/verify.
_hasher = PasswordHasher()


def hash_senha(senha: str) -> str:
    """Gera o hash argon2id completo (com sal e parametros embutidos)."""
    return _hasher.hash(senha)


def verifica_senha(hash_atual: str, senha: str) -> bool:
    """Compara senha com o hash. Hash corrompido conta como senha errada."""
    try:
        return _hasher.verify(hash_atual, senha)
    except (VerifyMismatchError, InvalidHashError):
        return False


def autenticar(db: Session, email: str, senha: str) -> Usuario | None:
    """Valida email+senha. Devolve o usuario ativo ou None.

    Email e comparado em minusculas e sem espacos nas pontas — o cadastro deve
    gravar do mesmo jeito, senao o login falha sem pista.
    """
    email_limpo = email.strip().lower()
    if not email_limpo or not senha:
        return None

    usuario = db.scalar(select(Usuario).where(Usuario.email == email_limpo))
    if usuario is None or not usuario.ativo:
        return None
    if not verifica_senha(usuario.senha_hash, senha):
        return None
    return usuario


def usuario_atual(request: Request, db: Session) -> Usuario | None:
    """Le o usuario da sessao. Sessao sem id, usuario apagado ou inativo = None."""
    usuario_id = request.session.get("usuario_id")
    if not usuario_id:
        return None
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not usuario.ativo:
        return None
    return usuario


def iniciar_sessao(request: Request, usuario: Usuario) -> None:
    """Abre sessao limpa. `clear()` evita fixation: id antigo nao sobrevive ao login."""
    request.session.clear()
    request.session["usuario_id"] = usuario.id
    request.session["csrf"] = secrets.token_urlsafe(24)


def encerrar_sessao(request: Request) -> None:
    request.session.clear()


def csrf_token(request: Request) -> str:
    """Token anti-CSRF da sessao. Cria um se ainda nao existir."""
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def csrf_valido(request: Request, enviado: str | None) -> bool:
    """Compara o token do form com o da sessao em tempo constante."""
    esperado = request.session.get("csrf")
    return bool(esperado and enviado and secrets.compare_digest(esperado, enviado))


def normalizar_email(email: str) -> str:
    """Forma canonica usada no cadastro e no login."""
    return email.strip().lower()
