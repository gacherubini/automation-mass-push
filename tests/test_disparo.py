"""Motor de disparo — sem rede, sem sleep de verdade."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import disparo
from app import ritmo
from app.auth import hash_senha
from app.evolution import (
    ChecagemNumero,
    MensagemEnviada,
    NumeroInvalido,
    ServicoIndisponivel,
)
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
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    with fabrica() as s:
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


def _cenario(
    sessao: Session,
    *,
    status_campanha: StatusCampanha = StatusCampanha.RODANDO,
    status_conexao: StatusConexao = StatusConexao.CONECTADA,
    modelos: list[str] | None = None,
    leads: list[tuple[str, str]] | None = None,
) -> tuple[Usuario, Conexao, Campanha]:
    usuario = Usuario(
        email="dono@exemplo.com",
        senha_hash=hash_senha("x"),
        nome="Dono",
    )
    sessao.add(usuario)
    sessao.flush()
    conexao = Conexao(
        usuario_id=usuario.id,
        nome_instancia="dono-1",
        status=status_conexao,
        numero="5551999999999",
        conectada_em=datetime.now(timezone.utc) - timedelta(days=10),
    )
    sessao.add(conexao)
    sessao.flush()
    campanha = Campanha(
        usuario_id=usuario.id,
        conexao_id=conexao.id,
        nome="Pets",
        modelos=modelos
        or ["Oi {nome}!", "Ola {nome}, de {categoria}!"],
        status=status_campanha,
        intervalo_min_seg=120,
        intervalo_max_seg=120,
        teto_diario=40,
        hora_inicio=time(0, 0),
        hora_fim=time(23, 59),
        dias_uteis_apenas=False,
    )
    sessao.add(campanha)
    sessao.flush()
    if leads is None:
        leads = [
            ("Bicho Mania", "5551998984086"),
            ("Agropet", "5551998581025"),
        ]
    for nome, fone in leads:
        sessao.add(
            Lead(
                campanha_id=campanha.id,
                nome=nome,
                telefone=fone,
                categoria="Pet shop",
            )
        )
    sessao.commit()
    return usuario, conexao, campanha


class _EvoFake:
    def __init__(
        self,
        *,
        existem: dict[str, bool] | None = None,
        falha_envio: Exception | None = None,
        falha_checagem: Exception | None = None,
    ):
        self.existem = existem or {}
        self.falha_envio = falha_envio
        self.falha_checagem = falha_checagem
        self.envios: list[tuple[str, str, str]] = []
        self.checagens: list[list[str]] = []

    def checar_numeros(self, nome: str, numeros):
        if self.falha_checagem:
            raise self.falha_checagem
        self.checagens.append(list(numeros))
        out = []
        for n in numeros:
            ok = self.existem.get(n, True)
            out.append(
                ChecagemNumero(
                    consultado=n,
                    existe=ok,
                    jid=f"{n}@s.whatsapp.net" if ok else "",
                    numero=n if ok else "",
                )
            )
        return out

    def enviar_texto(self, nome, numero, texto, **kwargs):
        if self.falha_envio:
            raise self.falha_envio
        self.envios.append((nome, numero, texto))
        return MensagemEnviada(
            id_mensagem=f"ID{len(self.envios)}",
            jid=f"{numero}@s.whatsapp.net",
            numero=numero,
            status="PENDING",
        )

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


# Meio da janela, dia util, com folga de horario.
_AGORA = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)  # segunda


class TestProcessarProximo:
    def test_envia_grava_mensagem_e_ja_contatado(self, sessao: Session):
        _u, _c, campanha = _cenario(sessao)
        evo = _EvoFake()
        r = disparo.processar_proximo(sessao, campanha, evo, agora=_AGORA, sorteio=None)
        assert r.acao == "enviado"
        assert r.espera_seg == 120
        assert len(evo.envios) == 1

        lead = sessao.get(Lead, r.lead_id)
        assert lead is not None
        assert lead.status is StatusLead.ENVIADO
        msg = sessao.scalar(select(Mensagem).where(Mensagem.lead_id == lead.id))
        assert msg is not None
        assert msg.status_entrega is StatusEntrega.ENVIADA
        assert "Bicho Mania" in msg.texto or "Oi" in msg.texto or "Ola" in msg.texto
        assert disparo.em_ja_contatado(sessao, campanha.usuario_id, lead.telefone)

    def test_respeita_optout(self, sessao: Session):
        usuario, _c, campanha = _cenario(sessao)
        sessao.add(OptOut(usuario_id=usuario.id, telefone="5551998984086", motivo="parar"))
        sessao.commit()
        evo = _EvoFake()
        r = disparo.processar_proximo(sessao, campanha, evo, agora=_AGORA)
        assert r.acao == "optout"
        assert evo.envios == []
        lead = sessao.get(Lead, r.lead_id)
        assert lead is not None
        assert lead.status is StatusLead.OPTOUT

    def test_nao_repete_ja_contatado(self, sessao: Session):
        usuario, _c, campanha = _cenario(sessao)
        sessao.add(
            JaContatado(usuario_id=usuario.id, telefone="5551998984086", campanha_id=None)
        )
        sessao.commit()
        evo = _EvoFake()
        r = disparo.processar_proximo(sessao, campanha, evo, agora=_AGORA)
        assert r.acao == "ja_contatado"
        assert evo.envios == []

    def test_sem_whatsapp_marca_e_nao_envia(self, sessao: Session):
        _u, _c, campanha = _cenario(sessao)
        evo = _EvoFake(existem={"5551998984086": False, "555198984086": False})
        r = disparo.processar_proximo(sessao, campanha, evo, agora=_AGORA)
        assert r.acao == "sem_whatsapp"
        assert evo.envios == []
        lead = sessao.get(Lead, r.lead_id)
        assert lead is not None
        assert lead.status is StatusLead.SEM_WHATSAPP

    def test_falha_de_envio_nao_reenvia_e_grava_falha(self, sessao: Session):
        _u, _c, campanha = _cenario(sessao)
        evo = _EvoFake(falha_envio=ServicoIndisponivel("timeout"))
        r = disparo.processar_proximo(sessao, campanha, evo, agora=_AGORA)
        assert r.acao == "falhou"
        lead = sessao.get(Lead, r.lead_id)
        assert lead is not None
        assert lead.status is StatusLead.FALHOU
        msg = sessao.scalar(select(Mensagem))
        assert msg is not None
        assert msg.status_entrega is StatusEntrega.FALHOU
        # Segundo passo pega o PROXIMO lead, nao o mesmo.
        r2 = disparo.processar_proximo(sessao, campanha, _EvoFake(), agora=_AGORA)
        assert r2.lead_id != r.lead_id
        assert r2.acao == "enviado"

    def test_freio_permanente_pausa_campanha(self, sessao: Session):
        _u, conexao, campanha = _cenario(sessao, leads=[("A", "5551998984086")])
        # Simula historico ruim: 20 entregues, 3 bloqueios (>2%).
        for i in range(20):
            lead = Lead(
                campanha_id=campanha.id,
                nome=f"L{i}",
                telefone=f"55519989{i:05d}",
                status=StatusLead.ENVIADO,
            )
            sessao.add(lead)
            sessao.flush()
            sessao.add(
                Mensagem(
                    lead_id=lead.id,
                    campanha_id=campanha.id,
                    texto="x",
                    status_entrega=(
                        StatusEntrega.BLOQUEADA if i < 3 else StatusEntrega.ENTREGUE
                    ),
                )
            )
        sessao.commit()

        r = disparo.processar_proximo(sessao, campanha, _EvoFake(), agora=_AGORA)
        assert r.acao == "pausada"
        assert "bloqueio" in r.motivo.lower()
        sessao.refresh(campanha)
        assert campanha.status is StatusCampanha.PAUSADA
        assert campanha.motivo_pausa

    def test_fora_da_janela_aguarda_sem_enviar(self, sessao: Session):
        _u, _c, campanha = _cenario(sessao)
        campanha.hora_inicio = time(9, 0)
        campanha.hora_fim = time(18, 0)
        campanha.dias_uteis_apenas = True
        sessao.commit()
        # Domingo 10h
        domingo = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
        r = disparo.processar_proximo(sessao, campanha, _EvoFake(), agora=domingo)
        assert r.acao == "aguardando"
        assert r.espera_seg == disparo.ESPERA_QUANDO_BLOQUEADO_SEG

    def test_fila_vazia_conclui(self, sessao: Session):
        _u, _c, campanha = _cenario(sessao, leads=[])
        r = disparo.processar_proximo(sessao, campanha, _EvoFake(), agora=_AGORA)
        assert r.acao == "concluida"
        sessao.refresh(campanha)
        assert campanha.status is StatusCampanha.CONCLUIDA

    def test_retomar_nao_duplica_enviado(self, sessao: Session):
        _u, _c, campanha = _cenario(sessao)
        evo = _EvoFake()
        r1 = disparo.processar_proximo(sessao, campanha, evo, agora=_AGORA)
        assert r1.acao == "enviado"
        # Pausa e retoma
        campanha.status = StatusCampanha.PAUSADA
        sessao.commit()
        campanha.status = StatusCampanha.RODANDO
        sessao.commit()
        r2 = disparo.processar_proximo(sessao, campanha, evo, agora=_AGORA)
        assert r2.acao == "enviado"
        assert r2.lead_id != r1.lead_id
        assert len(evo.envios) == 2


class TestOptoutTexto:
    def test_detecta_pedidos(self):
        assert disparo.e_pedido_optout("Por favor PARA de mandar")
        assert disparo.e_pedido_optout("não tenho interesse")
        assert disparo.e_pedido_optout("Sair")
        assert not disparo.e_pedido_optout("Adorei o paraíso de vocês")
        assert not disparo.e_pedido_optout("Quero saber mais")


class TestProcessarResposta:
    def test_marca_respondeu_e_optout(self, sessao: Session):
        usuario, conexao, campanha = _cenario(sessao, leads=[("Bicho", "5551998984086")])
        lead = sessao.scalar(select(Lead))
        assert lead is not None
        lead.status = StatusLead.ENVIADO
        sessao.add(
            Mensagem(
                lead_id=lead.id,
                campanha_id=campanha.id,
                texto="oi",
                status_entrega=StatusEntrega.ENVIADA,
                id_externo="MSG1",
            )
        )
        sessao.commit()

        tocado = disparo.processar_resposta_recebida(
            sessao,
            nome_instancia=conexao.nome_instancia,
            numero="5551998984086",
            texto="Oi, tenho interesse",
        )
        assert tocado is not None
        assert tocado.status is StatusLead.RESPONDEU
        msg = sessao.scalar(select(Mensagem))
        assert msg is not None
        assert msg.status_entrega is StatusEntrega.ENTREGUE

        tocado2 = disparo.processar_resposta_recebida(
            sessao,
            nome_instancia=conexao.nome_instancia,
            numero="5551998984086",
            texto="para de mandar",
        )
        assert tocado2 is not None
        assert tocado2.status is StatusLead.OPTOUT
        assert disparo.em_optout(sessao, usuario.id, "5551998984086")


class TestPodeIniciar:
    def test_ok_e_falhas(self, sessao: Session):
        _u, _c, campanha = _cenario(sessao, status_campanha=StatusCampanha.RASCUNHO)
        ok, _ = disparo.pode_iniciar(sessao, campanha)
        assert ok is True

        campanha.modelos = []
        sessao.commit()
        ok, motivo = disparo.pode_iniciar(sessao, campanha)
        assert ok is False
        assert "modelo" in motivo.lower()
