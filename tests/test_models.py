"""Modelos e a sessao em SQLite em memoria.

Nao e o Postgres de producao, mas e o que valida constraints, uniques e o
espelhamento do Perfil de ritmo sem depender de Docker.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import ritmo
from app.models import (
    Base,
    Campanha,
    Conexao,
    JaContatado,
    Lead,
    Mensagem,
    OptOut,
    StatusCampanha,
    StatusConexao,
    StatusEntrega,
    StatusLead,
    Usuario,
)


@pytest.fixture
def sessao() -> Session:
    # StaticPool + check_same_thread: a mesma conexao em memoria serve a todas
    # as sessoes do teste. Sem isso o SQLite abre um banco vazio por conexao.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite so aplica FK se pedir. Sem isto as cascades e uniques de FK
    # passariam batido e o teste mentiria.
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


def _usuario(sessao: Session, email: str = "dono@exemplo.com") -> Usuario:
    usuario = Usuario(email=email, senha_hash="hash-fake", nome="Dono")
    sessao.add(usuario)
    sessao.flush()
    return usuario


class TestCriacaoBasica:
    def test_as_sete_tabelas_existem(self, sessao: Session):
        usuario = _usuario(sessao)
        conexao = Conexao(
            usuario_id=usuario.id,
            nome_instancia="dono-1",
            status=StatusConexao.CONECTADA,
            numero="5551998984086",
            conectada_em=datetime.now(timezone.utc) - timedelta(days=5),
        )
        sessao.add(conexao)
        sessao.flush()

        campanha = Campanha(
            usuario_id=usuario.id,
            conexao_id=conexao.id,
            nome="Pets Canoas",
            modelos=["Oi {nome}!", "Ola {nome}!"],
        )
        sessao.add(campanha)
        sessao.flush()

        lead = Lead(
            campanha_id=campanha.id,
            nome="Bicho Mania",
            telefone="5551998984086",
            telefone_exibicao="(51) 99898-4086",
            categoria="Pet shop",
        )
        sessao.add(lead)
        sessao.flush()

        mensagem = Mensagem(
            lead_id=lead.id,
            campanha_id=campanha.id,
            texto="Oi Bicho Mania!",
            status_entrega=StatusEntrega.ENVIADA,
            id_externo="ABC123",
        )
        sessao.add(mensagem)
        sessao.add(OptOut(usuario_id=usuario.id, telefone="5551997655755", motivo="parar"))
        sessao.add(
            JaContatado(
                usuario_id=usuario.id,
                telefone="5551998581025",
                campanha_id=campanha.id,
            )
        )
        sessao.commit()

        assert sessao.scalar(select(Usuario).where(Usuario.email == "dono@exemplo.com"))
        assert campanha.status is StatusCampanha.RASCUNHO
        assert lead.status is StatusLead.PENDENTE
        assert conexao.idade_dias >= 5
        assert conexao.pode_enviar is True


class TestPerfilDeRitmo:
    def test_perfil_espelha_colunas_da_campanha(self, sessao: Session):
        usuario = _usuario(sessao)
        campanha = Campanha(
            usuario_id=usuario.id,
            nome="Teste ritmo",
            modelos=["oi"],
            teto_diario=80,
            intervalo_min_seg=150,
            intervalo_max_seg=400,
            hora_inicio=time(10, 0),
            hora_fim=time(17, 0),
            dias_uteis_apenas=False,
            respeitar_aquecimento=False,
        )
        sessao.add(campanha)
        sessao.commit()

        perfil = campanha.perfil()
        assert isinstance(perfil, ritmo.Perfil)
        assert perfil.teto_diario == 80
        assert perfil.intervalo_min_seg == 150
        assert perfil.intervalo_max_seg == 400
        assert perfil.hora_inicio == time(10, 0)
        assert perfil.dias_uteis_apenas is False

    def test_aplicar_perfil_grava_de_volta(self, sessao: Session):
        usuario = _usuario(sessao)
        campanha = Campanha(usuario_id=usuario.id, nome="A", modelos=["x"])
        sessao.add(campanha)
        sessao.flush()

        novo = ritmo.Perfil(teto_diario=25, intervalo_min_seg=180, intervalo_max_seg=360)
        campanha.aplicar_perfil(novo)
        sessao.commit()

        reloaded = sessao.get(Campanha, campanha.id)
        assert reloaded is not None
        assert reloaded.teto_diario == 25
        assert reloaded.intervalo_min_seg == 180
        assert reloaded.intervalo_max_seg == 360


class TestUniquesEIntegridade:
    def test_email_de_usuario_e_unico(self, sessao: Session):
        sessao.add(Usuario(email="a@b.com", senha_hash="x", nome="A"))
        sessao.commit()
        sessao.add(Usuario(email="a@b.com", senha_hash="y", nome="B"))
        with pytest.raises(IntegrityError):
            sessao.commit()

    def test_optout_global_por_usuario(self, sessao: Session):
        usuario = _usuario(sessao)
        sessao.add(OptOut(usuario_id=usuario.id, telefone="5551998984086"))
        sessao.commit()
        sessao.add(OptOut(usuario_id=usuario.id, telefone="5551998984086"))
        with pytest.raises(IntegrityError):
            sessao.commit()

    def test_ja_contatado_global_por_usuario(self, sessao: Session):
        usuario = _usuario(sessao)
        sessao.add(JaContatado(usuario_id=usuario.id, telefone="5551998581025"))
        sessao.commit()
        sessao.add(JaContatado(usuario_id=usuario.id, telefone="5551998581025"))
        with pytest.raises(IntegrityError):
            sessao.commit()

    def test_apagar_campanha_nao_apaga_ja_contatado(self, sessao: Session):
        # A memoria global e a promessa; a origem e so informacao util.
        usuario = _usuario(sessao)
        campanha = Campanha(usuario_id=usuario.id, nome="Temp", modelos=["oi"])
        sessao.add(campanha)
        sessao.flush()
        registro = JaContatado(
            usuario_id=usuario.id,
            telefone="5551998984086",
            campanha_id=campanha.id,
        )
        sessao.add(registro)
        sessao.commit()

        sessao.delete(campanha)
        sessao.commit()

        # expire_on_commit=False mantem o valor antigo em memoria; forca releitura
        # do banco para ver o ON DELETE SET NULL de verdade.
        sessao.expire(registro)
        reloaded = sessao.get(JaContatado, registro.id)
        assert reloaded is not None
        assert reloaded.telefone == "5551998984086"
        assert reloaded.campanha_id is None

    def test_lead_duplicado_na_mesma_campanha_e_barrado(self, sessao: Session):
        usuario = _usuario(sessao)
        campanha = Campanha(usuario_id=usuario.id, nome="Dup", modelos=["oi"])
        sessao.add(campanha)
        sessao.flush()
        sessao.add(
            Lead(campanha_id=campanha.id, nome="Bicho Mania", telefone="5551998984086")
        )
        sessao.commit()
        sessao.add(
            Lead(campanha_id=campanha.id, nome="Bicho Mania 2", telefone="5551998984086")
        )
        with pytest.raises(IntegrityError):
            sessao.commit()

    def test_varias_linhas_sem_telefone_convivem(self, sessao: Session):
        # NULL nao colide na unique (campanha_id, telefone).
        usuario = _usuario(sessao)
        campanha = Campanha(usuario_id=usuario.id, nome="Sem fone", modelos=["oi"])
        sessao.add(campanha)
        sessao.flush()
        sessao.add(Lead(campanha_id=campanha.id, nome="Loja A", telefone=None))
        sessao.add(Lead(campanha_id=campanha.id, nome="Loja B", telefone=None))
        sessao.commit()
        assert sessao.scalars(select(Lead)).all().__len__() == 2


class TestIdadeDaConexao:
    def test_sem_conectada_em_conta_como_recem_nascida(self, sessao: Session):
        usuario = _usuario(sessao)
        conexao = Conexao(usuario_id=usuario.id, nome_instancia="nova")
        sessao.add(conexao)
        sessao.commit()
        assert conexao.idade_dias == 0
        assert conexao.pode_enviar is False
