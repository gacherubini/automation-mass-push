"""Rotas do dashboard com TestClient + SQLite em memoria."""

from __future__ import annotations

import io
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_senha
from app.config import Configuracao
from app.db import get_sessao
from app.main import criar_app
from app.models import (
    AutorInteracao,
    Base,
    Campanha,
    Conexao,
    Conversa,
    Interacao,
    JaContatado,
    Lead,
    Mensagem,
    ModoIA,
    OptOut,
    StatusCampanha,
    StatusConexao,
    StatusConversa,
    StatusEntrega,
    StatusInteracao,
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


class _GerenciadorConversaFake:
    def __init__(self) -> None:
        self.processadas: list[int] = []
        self.aprovadas: list[int] = []
        self.manuais: list[tuple[int, str]] = []

    def processar(self, interacao_id: int):
        self.processadas.append(interacao_id)
        return SimpleNamespace(acao="rascunho", motivo="")

    def aprovar(self, interacao_id: int):
        self.aprovadas.append(interacao_id)
        return SimpleNamespace(acao="enviada", motivo="")

    def enviar_manual(self, conversa_id: int, texto: str):
        self.manuais.append((conversa_id, texto))
        return SimpleNamespace(acao="enviada", motivo="")


@pytest.fixture
def gerenciador() -> _GerenciadorFake:
    return _GerenciadorFake()


@pytest.fixture
def gerenciador_conversa() -> _GerenciadorConversaFake:
    return _GerenciadorConversaFake()


@pytest.fixture
def client(fabrica, gerenciador, gerenciador_conversa) -> Iterator[TestClient]:
    app = criar_app(
        fabrica_evolution=lambda: (_ for _ in ()).throw(
            RuntimeError("evolution nao deveria ser chamada neste teste")
        ),
        gerenciador_disparo=gerenciador,  # type: ignore[arg-type]
        gerenciador_conversa=gerenciador_conversa,  # type: ignore[arg-type]
        retomar_no_startup=False,
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
        c.gerenciador_conversa = gerenciador_conversa  # type: ignore[attr-defined]
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


def _login_usuario(client: TestClient, email: str, senha: str = "senha-forte") -> None:
    pagina = client.get("/login")
    resposta = client.post(
        "/login",
        data={"csrf": _csrf(pagina.text), "email": email, "senha": senha},
    )
    assert resposta.status_code == 200


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
            assert len(campanha.modelos) == 4
            assert all("automações de IA" in modelo for modelo in campanha.modelos)
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

    def test_configura_ia_em_rascunho_e_exibe_resultados(
        self, client: TestClient, fabrica, monkeypatch
    ):
        self._login(client, fabrica)
        with fabrica() as s:
            usuario = s.scalar(select(Usuario))
            campanha = Campanha(
                usuario_id=usuario.id,
                nome="Teste de abordagem",
                modelos=["Oi {nome}", "Ola {nome}"],
            )
            s.add(campanha)
            s.commit()
            campanha_id = campanha.id

        monkeypatch.setattr(
            "app.main.configuracao",
            lambda: Configuracao(gemini_api_key="chave-de-teste"),
        )
        pagina = client.get(f"/campanhas/{campanha_id}")
        assert pagina.status_code == 200
        assert "Qual mensagem funciona melhor?" in pagina.text
        assert "2. Respostas com IA" in pagina.text
        csrf = _csrf(pagina.text)

        resposta = client.post(
            f"/campanhas/{campanha_id}/ia",
            data={
                "csrf": csrf,
                "modo_ia": "rascunho",
                "prompt_ia": "Descobrir quem decide e transferir quando houver interesse.",
                "limite_respostas_ia": "3",
            },
            follow_redirects=False,
        )
        assert resposta.status_code == 303
        with fabrica() as s:
            campanha = s.get(Campanha, campanha_id)
            assert campanha.modo_ia == ModoIA.RASCUNHO
            assert campanha.limite_respostas_ia == 3
            assert "quem decide" in campanha.prompt_ia

    def test_nao_ativa_ia_sem_chave(self, client: TestClient, fabrica, monkeypatch):
        self._login(client, fabrica)
        with fabrica() as s:
            usuario = s.scalar(select(Usuario))
            campanha = Campanha(usuario_id=usuario.id, nome="Sem chave", modelos=[])
            s.add(campanha)
            s.commit()
            campanha_id = campanha.id

        monkeypatch.setattr("app.main.configuracao", lambda: Configuracao())
        pagina = client.get(f"/campanhas/{campanha_id}")
        resposta = client.post(
            f"/campanhas/{campanha_id}/ia",
            data={
                "csrf": _csrf(pagina.text),
                "modo_ia": "automatica",
                "prompt_ia": "Descobrir quem decide nesta loja e transferir.",
                "limite_respostas_ia": "4",
            },
            follow_redirects=False,
        )
        assert resposta.status_code == 303
        with fabrica() as s:
            assert s.get(Campanha, campanha_id).modo_ia == ModoIA.DESLIGADA

    def test_apaga_campanha_e_preserva_bloqueios_globais(
        self, client: TestClient, fabrica
    ):
        self._login(client, fabrica)
        with fabrica() as s:
            usuario = s.scalar(select(Usuario))
            campanha = Campanha(
                usuario_id=usuario.id,
                nome="Campanha descartavel",
                modelos=["Oi"],
                status=StatusCampanha.PAUSADA,
            )
            s.add(campanha)
            s.flush()
            lead = Lead(
                campanha_id=campanha.id,
                nome="Loja",
                telefone="5551999999911",
                status=StatusLead.RESPONDEU,
            )
            s.add(lead)
            s.flush()
            conversa = Conversa(lead_id=lead.id)
            mensagem = Mensagem(
                lead_id=lead.id,
                campanha_id=campanha.id,
                texto="Oi",
                status_entrega=StatusEntrega.ENVIADA,
            )
            memoria = JaContatado(
                usuario_id=usuario.id,
                telefone=lead.telefone,
                campanha_id=campanha.id,
            )
            optout = OptOut(
                usuario_id=usuario.id,
                telefone="5551999999922",
                motivo="parar",
            )
            s.add_all([conversa, mensagem, memoria, optout])
            s.commit()
            ids = (campanha.id, lead.id, conversa.id, mensagem.id, memoria.id, optout.id)

        pagina = client.get(f"/campanhas/{ids[0]}")
        assert "Apagar campanha" in pagina.text
        resposta = client.post(
            f"/campanhas/{ids[0]}/apagar",
            data={"csrf": _csrf(pagina.text)},
            follow_redirects=False,
        )

        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/app"
        with fabrica() as s:
            assert s.get(Campanha, ids[0]) is None
            assert s.get(Lead, ids[1]) is None
            assert s.get(Conversa, ids[2]) is None
            assert s.get(Mensagem, ids[3]) is None
            memoria = s.get(JaContatado, ids[4])
            assert memoria is not None
            assert memoria.campanha_id is None
            assert s.get(OptOut, ids[5]) is not None

    def test_nao_apaga_campanha_rodando(self, client: TestClient, fabrica):
        self._login(client, fabrica)
        with fabrica() as s:
            usuario = s.scalar(select(Usuario))
            campanha = Campanha(
                usuario_id=usuario.id,
                nome="Em andamento",
                modelos=["Oi"],
                status=StatusCampanha.RODANDO,
            )
            s.add(campanha)
            s.commit()
            campanha_id = campanha.id

        pagina = client.get(f"/campanhas/{campanha_id}")
        resposta = client.post(
            f"/campanhas/{campanha_id}/apagar",
            data={"csrf": _csrf(pagina.text)},
            follow_redirects=False,
        )

        assert resposta.status_code == 303
        assert resposta.headers["location"] == f"/campanhas/{campanha_id}"
        with fabrica() as s:
            assert s.get(Campanha, campanha_id) is not None


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

    def test_resposta_cria_conversa_e_agenda_ia_uma_vez(
        self, client: TestClient, fabrica, gerenciador_conversa
    ):
        with fabrica() as s:
            usuario = Usuario(
                email="ia@exemplo.com",
                senha_hash=hash_senha("senha-forte"),
                nome="IA",
            )
            s.add(usuario)
            s.flush()
            conexao = Conexao(
                usuario_id=usuario.id,
                nome_instancia="inst-ia",
                status=StatusConexao.CONECTADA,
            )
            s.add(conexao)
            s.flush()
            campanha = Campanha(
                usuario_id=usuario.id,
                conexao_id=conexao.id,
                nome="C IA",
                modelos=["oi"],
                modo_ia=ModoIA.RASCUNHO,
                prompt_ia="Descobrir quem decide e transferir para a equipe.",
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
            s.commit()
            lead_id = lead.id

        payload = {
            "event": "messages.upsert",
            "instance": "inst-ia",
            "data": {
                "key": {
                    "remoteJid": "5551998984086@s.whatsapp.net",
                    "fromMe": False,
                    "id": "R-IA-1",
                },
                "message": {"conversation": "Queria entender melhor"},
            },
        }
        primeira = client.post("/webhook/evolution", json=payload)
        segunda = client.post("/webhook/evolution", json=payload)

        assert primeira.json()["ia_agendada"] is True
        assert primeira.json()["nova"] is True
        assert segunda.json()["ia_agendada"] is False
        assert segunda.json()["nova"] is False
        assert len(gerenciador_conversa.processadas) == 1
        with fabrica() as s:
            conversa = s.scalar(select(Conversa).where(Conversa.lead_id == lead_id))
            entradas = s.scalars(
                select(Interacao).where(Interacao.id_externo == "R-IA-1")
            ).all()
            assert conversa is not None
            assert len(entradas) == 1
            assert entradas[0].autor == AutorInteracao.LEAD


class TestInbox:
    def test_lista_detalhe_aprovacao_e_resposta_manual(
        self, client: TestClient, fabrica, gerenciador_conversa
    ):
        with fabrica() as s:
            usuario = Usuario(
                email="inbox@exemplo.com",
                senha_hash=hash_senha("senha-forte"),
                nome="Inbox",
            )
            s.add(usuario)
            s.flush()
            campanha = Campanha(
                usuario_id=usuario.id,
                nome="Pets",
                modelos=["Oi"],
                modo_ia=ModoIA.RASCUNHO,
            )
            s.add(campanha)
            s.flush()
            lead = Lead(
                campanha_id=campanha.id,
                nome="Bicho Mania",
                telefone="5551998984086",
                status=StatusLead.RESPONDEU,
            )
            s.add(lead)
            s.flush()
            conversa = Conversa(
                lead_id=lead.id,
                status=StatusConversa.AGUARDANDO_HUMANO,
                resumo="Atendente respondeu; revisar sugestao.",
            )
            s.add(conversa)
            s.flush()
            s.add(
                Interacao(
                    conversa_id=conversa.id,
                    autor=AutorInteracao.LEAD,
                    status=StatusInteracao.RECEBIDA,
                    texto="Sou atendente, do que se trata?",
                    id_externo="IN-1",
                )
            )
            s.flush()
            rascunho = Interacao(
                conversa_id=conversa.id,
                autor=AutorInteracao.IA,
                status=StatusInteracao.RASCUNHO,
                texto="Quem cuida dessa parte por ai?",
            )
            s.add(rascunho)
            s.commit()
            conversa_id = conversa.id
            rascunho_id = rascunho.id

        _login_usuario(client, "inbox@exemplo.com")
        lista = client.get("/conversas")
        assert lista.status_code == 200
        assert "Bicho Mania" in lista.text
        assert "precisam" not in lista.text.lower() or "Conversa" in lista.text

        detalhe = client.get(f"/conversas/{conversa_id}")
        assert detalhe.status_code == 200
        assert "Quem cuida dessa parte" in detalhe.text
        assert "Aprovar e enviar" in detalhe.text
        csrf = _csrf(detalhe.text)

        aprovado = client.post(
            f"/conversas/{conversa_id}/aprovar/{rascunho_id}",
            data={"csrf": csrf},
            follow_redirects=False,
        )
        assert aprovado.status_code == 303
        assert gerenciador_conversa.aprovadas == [rascunho_id]

        manual = client.post(
            f"/conversas/{conversa_id}/responder",
            data={"csrf": csrf, "texto": "Vou assumir daqui, obrigado."},
            follow_redirects=False,
        )
        assert manual.status_code == 303
        assert gerenciador_conversa.manuais == [
            (conversa_id, "Vou assumir daqui, obrigado.")
        ]

    def test_usuario_nao_abre_conversa_de_outro_usuario(self, client: TestClient, fabrica):
        with fabrica() as s:
            dono = Usuario(
                email="dono-inbox@exemplo.com",
                senha_hash=hash_senha("senha-forte"),
                nome="Dono",
            )
            intruso = Usuario(
                email="intruso@exemplo.com",
                senha_hash=hash_senha("senha-forte"),
                nome="Intruso",
            )
            s.add_all([dono, intruso])
            s.flush()
            campanha = Campanha(usuario_id=dono.id, nome="Privada", modelos=[])
            s.add(campanha)
            s.flush()
            lead = Lead(campanha_id=campanha.id, nome="Loja", telefone="5551999999999")
            s.add(lead)
            s.flush()
            conversa = Conversa(lead_id=lead.id)
            s.add(conversa)
            s.commit()
            conversa_id = conversa.id

        _login_usuario(client, "intruso@exemplo.com")
        resposta = client.get(f"/conversas/{conversa_id}", follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/conversas"
