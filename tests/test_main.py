"""Rotas do dashboard com TestClient + SQLite em memoria."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_senha
from app.db import get_sessao
from app.main import criar_app
from app.models import (
    Base,
    Campanha,
    Conexao,
    Lead,
    Mensagem,
    StatusCampanha,
    StatusConexao,
    StatusEntrega,
    StatusLead,
    Usuario,
)
from datetime import datetime, timedelta, timezone


@pytest.fixture
def engine_teste():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conexao, _):
        cur = dbapi_conexao.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def fabrica(engine_teste):
    return sessionmaker(bind=engine_teste, expire_on_commit=False)


class _GerenciadorFake:
    def __init__(self) -> None:
        self.iniciadas: list[int] = []

    def iniciar(self, campanha_id: int) -> bool:
        self.iniciadas.append(campanha_id)
        return True

    def esta_rodando(self, campanha_id: int) -> bool:
        return campanha_id in self.iniciadas


@pytest.fixture
def gerenciador() -> _GerenciadorFake:
    return _GerenciadorFake()


@pytest.fixture
def client(fabrica, gerenciador) -> Iterator[TestClient]:
    app = criar_app(
        fabrica_evolution=lambda: (_ for _ in ()).throw(
            RuntimeError("evolution nao deveria ser chamada neste teste")
        ),
        gerenciador_disparo=gerenciador,  # type: ignore[arg-type]
    )

    def _sessao() -> Iterator[Session]:
        with fabrica() as s:
            try:
                yield s
            except Exception:
                s.rollback()
                raise

    app.dependency_overrides[get_sessao] = _sessao
    with TestClient(app) as c:
        c.gerenciador = gerenciador  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _xlsx_minimo() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Nome", "Telefone", "Categoria", "Endereco", "Busca"])
    ws.append(["Bicho Mania", "(51) 99898-4086", "Pet shop", "Canoas", "pet shop"])
    ws.append(["Petz Canoas", "(51) 3052-0478", "Pet shop", "Canoas", "pet shop"])  # fixo
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csrf(html: str) -> str:
    # input hidden name="csrf" value="..."
    marca = 'name="csrf" value="'
    i = html.find(marca)
    assert i >= 0, "csrf nao encontrado no HTML"
    j = html.find('"', i + len(marca))
    return html[i + len(marca) : j]


class TestBootstrapELogin:
    def test_bootstrap_cria_primeiro_usuario_e_loga(self, client: TestClient, fabrica):
        r = client.get("/bootstrap")
        assert r.status_code == 200
        csrf = _csrf(r.text)

        r = client.post(
            "/bootstrap",
            data={
                "csrf": csrf,
                "nome": "Dono",
                "email": "Dono@Exemplo.com",
                "senha": "senha-forte",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/app"

        with fabrica() as s:
            u = s.scalar(select(Usuario).where(Usuario.email == "dono@exemplo.com"))
            assert u is not None
            assert u.nome == "Dono"

        r = client.get("/app")
        assert r.status_code == 200
        assert "Olá, Dono" in r.text or "Ola, Dono" in r.text or "Dono" in r.text

    def test_bootstrap_bloqueado_depois_do_primeiro(self, client: TestClient, fabrica):
        with fabrica() as s:
            s.add(
                Usuario(
                    email="ja@existe.com",
                    senha_hash=hash_senha("senha-forte"),
                    nome="Ja",
                )
            )
            s.commit()

        r = client.get("/bootstrap", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_login_e_logout(self, client: TestClient, fabrica):
        with fabrica() as s:
            s.add(
                Usuario(
                    email="dono@exemplo.com",
                    senha_hash=hash_senha("senha-forte"),
                    nome="Dono",
                )
            )
            s.commit()

        r = client.get("/login")
        csrf = _csrf(r.text)
        r = client.post(
            "/login",
            data={"csrf": csrf, "email": "dono@exemplo.com", "senha": "senha-forte"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/app"

        r = client.get("/app")
        assert r.status_code == 200
        csrf = _csrf(r.text)
        r = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
        assert r.status_code == 303

        r = client.get("/app", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"


class TestCampanha:
    def _login(self, client: TestClient, fabrica) -> None:
        with fabrica() as s:
            s.add(
                Usuario(
                    email="dono@exemplo.com",
                    senha_hash=hash_senha("senha-forte"),
                    nome="Dono",
                )
            )
            s.commit()
        r = client.get("/login")
        csrf = _csrf(r.text)
        client.post(
            "/login",
            data={"csrf": csrf, "email": "dono@exemplo.com", "senha": "senha-forte"},
        )

    def test_importa_planilha_e_salva_modelos(self, client: TestClient, fabrica):
        self._login(client, fabrica)

        r = client.get("/campanhas/nova")
        assert r.status_code == 200
        csrf = _csrf(r.text)

        r = client.post(
            "/campanhas/nova",
            data={"csrf": csrf, "nome": "Pets Canoas", "conexao_id": ""},
            files={
                "arquivo": (
                    "lojas.xlsx",
                    _xlsx_minimo(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/campanhas/")

        with fabrica() as s:
            campanha = s.scalar(select(Campanha))
            assert campanha is not None
            assert campanha.nome == "Pets Canoas"
            leads = s.scalars(select(Lead).where(Lead.campanha_id == campanha.id)).all()
            # fixo descartado; so o celular entra
            assert len(leads) == 1
            assert leads[0].nome == "Bicho Mania"
            assert leads[0].telefone == "5551998984086"

        r = client.get(loc)
        assert r.status_code == 200
        assert "Bicho Mania" in r.text or "1 leads" in r.text or "leads" in r.text
        csrf = _csrf(r.text)

        r = client.post(
            f"{loc}/modelos",
            data={
                "csrf": csrf,
                "modelos_texto": (
                    "Oi! Vi a {nome} no Maps.\n---\n"
                    "Ola {nome}, de {categoria}!"
                ),
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

        with fabrica() as s:
            campanha = s.scalar(select(Campanha))
            assert campanha is not None
            assert len(campanha.modelos) == 2

        r = client.get(loc)
        assert "Prévia" in r.text or "Previa" in r.text or "Bicho Mania" in r.text

    def test_area_logada_exige_login(self, client: TestClient):
        r = client.get("/campanhas/nova", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_iniciar_e_pausar_campanha(self, client: TestClient, fabrica, gerenciador):
        self._login(client, fabrica)
        with fabrica() as s:
            u = s.scalar(select(Usuario))
            assert u is not None
            conexao = Conexao(
                usuario_id=u.id,
                nome_instancia="dono-1",
                status=StatusConexao.CONECTADA,
                numero="5551999999999",
                conectada_em=datetime.now(timezone.utc) - timedelta(days=5),
            )
            s.add(conexao)
            s.flush()
            campanha = Campanha(
                usuario_id=u.id,
                conexao_id=conexao.id,
                nome="Teste",
                modelos=["Oi {nome}!"],
                status=StatusCampanha.RASCUNHO,
            )
            s.add(campanha)
            s.flush()
            s.add(
                Lead(
                    campanha_id=campanha.id,
                    nome="Bicho Mania",
                    telefone="5551998984086",
                )
            )
            s.commit()
            cid = campanha.id

        r = client.get(f"/campanhas/{cid}")
        csrf = _csrf(r.text)
        r = client.post(
            f"/campanhas/{cid}/iniciar",
            data={"csrf": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert cid in gerenciador.iniciadas

        with fabrica() as s:
            c = s.get(Campanha, cid)
            assert c is not None
            assert c.status is StatusCampanha.RODANDO

        r = client.get(f"/campanhas/{cid}")
        csrf = _csrf(r.text)
        r = client.post(
            f"/campanhas/{cid}/pausar",
            data={"csrf": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 303
        with fabrica() as s:
            c = s.get(Campanha, cid)
            assert c is not None
            assert c.status is StatusCampanha.PAUSADA

        r = client.get(f"/campanhas/{cid}/progresso")
        assert r.status_code == 200
        assert r.json()["status"] == "pausada"
        assert r.json()["pendentes"] == 1


class TestWebhook:
    def test_resposta_marca_lead_e_optout(self, client: TestClient, fabrica):
        with fabrica() as s:
            u = Usuario(
                email="w@exemplo.com",
                senha_hash=hash_senha("senha-forte"),
                nome="W",
            )
            s.add(u)
            s.flush()
            conexao = Conexao(
                usuario_id=u.id,
                nome_instancia="inst-w",
                status=StatusConexao.CONECTADA,
            )
            s.add(conexao)
            s.flush()
            campanha = Campanha(
                usuario_id=u.id,
                conexao_id=conexao.id,
                nome="C",
                modelos=["oi"],
            )
            s.add(campanha)
            s.flush()
            lead = Lead(
                campanha_id=campanha.id,
                nome="Bicho Mania",
                telefone="5551998984086",
                status=StatusLead.ENVIADO,
            )
            s.add(lead)
            s.flush()
            s.add(
                Mensagem(
                    lead_id=lead.id,
                    campanha_id=campanha.id,
                    texto="oi",
                    status_entrega=StatusEntrega.ENVIADA,
                    id_externo="MSG99",
                )
            )
            s.commit()
            lead_id = lead.id

        payload = {
            "event": "messages.upsert",
            "instance": "inst-w",
            "data": {
                "key": {
                    "remoteJid": "5551998984086@s.whatsapp.net",
                    "fromMe": False,
                    "id": "R1",
                },
                "message": {"conversation": "para de mandar por favor"},
            },
        }
        r = client.post("/webhook/evolution", json=payload)
        assert r.status_code == 200
        assert r.json()["ok"] is True

        with fabrica() as s:
            lead = s.get(Lead, lead_id)
            assert lead is not None
            assert lead.status is StatusLead.OPTOUT
