"""Auth: hash argon2, login e CSRF — sem HTTP ainda."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import (
    autenticar,
    csrf_token,
    csrf_valido,
    encerrar_sessao,
    hash_senha,
    iniciar_sessao,
    normalizar_email,
    usuario_atual,
    verifica_senha,
)
from app.models import Base, Usuario


@pytest.fixture
def sessao() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _ligar_fk(dbapi_conexao, _):
        cursor = dbapi_conexao.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    with fabrica() as s:
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


def _request(sessao_dict: dict | None = None):
    """Request minimo com o dict de sessao que o Starlette colocaria."""
    return SimpleNamespace(session=sessao_dict if sessao_dict is not None else {})


class TestSenha:
    def test_hash_e_verifica(self):
        h = hash_senha("segredo-forte")
        assert h.startswith("$argon2")
        assert verifica_senha(h, "segredo-forte") is True
        assert verifica_senha(h, "outra") is False

    def test_hash_corrompido_nao_estoura(self):
        assert verifica_senha("isto-nao-e-argon2", "x") is False


class TestAutenticar:
    def test_login_ok(self, sessao: Session):
        u = Usuario(
            email="dono@exemplo.com",
            senha_hash=hash_senha("senha123"),
            nome="Dono",
        )
        sessao.add(u)
        sessao.commit()

        achado = autenticar(sessao, "  Dono@Exemplo.com ", "senha123")
        assert achado is not None
        assert achado.id == u.id

    def test_senha_errada(self, sessao: Session):
        sessao.add(
            Usuario(
                email="dono@exemplo.com",
                senha_hash=hash_senha("senha123"),
                nome="Dono",
            )
        )
        sessao.commit()
        assert autenticar(sessao, "dono@exemplo.com", "errada") is None

    def test_usuario_inativo(self, sessao: Session):
        sessao.add(
            Usuario(
                email="inativo@exemplo.com",
                senha_hash=hash_senha("senha123"),
                nome="X",
                ativo=False,
            )
        )
        sessao.commit()
        assert autenticar(sessao, "inativo@exemplo.com", "senha123") is None

    def test_email_inexistente(self, sessao: Session):
        assert autenticar(sessao, "ninguem@exemplo.com", "x") is None


class TestSessao:
    def test_iniciar_e_ler_usuario(self, sessao: Session):
        u = Usuario(
            email="a@b.com",
            senha_hash=hash_senha("x"),
            nome="A",
        )
        sessao.add(u)
        sessao.commit()

        req = _request({"usuario_id": 999, "csrf": "velho"})
        iniciar_sessao(req, u)
        # clear() apagou o id antigo (session fixation).
        assert req.session["usuario_id"] == u.id
        assert req.session["csrf"] != "velho"

        atual = usuario_atual(req, sessao)
        assert atual is not None
        assert atual.email == "a@b.com"

    def test_usuario_inativo_na_sessao_vira_none(self, sessao: Session):
        u = Usuario(
            email="a@b.com",
            senha_hash=hash_senha("x"),
            nome="A",
            ativo=False,
        )
        sessao.add(u)
        sessao.commit()
        req = _request({"usuario_id": u.id})
        assert usuario_atual(req, sessao) is None

    def test_encerrar_limpa_tudo(self, sessao: Session):
        u = Usuario(email="a@b.com", senha_hash=hash_senha("x"), nome="A")
        sessao.add(u)
        sessao.commit()
        req = _request()
        iniciar_sessao(req, u)
        encerrar_sessao(req)
        assert req.session == {}
        assert usuario_atual(req, sessao) is None


class TestCsrf:
    def test_token_criado_sob_demanda_e_validado(self):
        req = _request()
        token = csrf_token(req)
        assert token
        assert csrf_valido(req, token) is True
        assert csrf_valido(req, "outro") is False
        assert csrf_valido(req, None) is False
        assert csrf_valido(req, "") is False

    def test_mesmo_token_nas_chamadas_seguintes(self):
        req = _request()
        a = csrf_token(req)
        b = csrf_token(req)
        assert a == b


class TestEmail:
    def test_normalizar(self):
        assert normalizar_email("  Foo@Bar.COM ") == "foo@bar.com"
