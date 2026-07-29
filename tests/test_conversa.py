"""Fluxo de conversa com banco real e provedores falsos."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.conversa import RESPOSTA_TRANSPARENCIA, GerenciadorConversa, registrar_entrada
from app.evolution import MensagemEnviada
from app.gemini import DecisaoIA, ErroGemini
from app.models import (
    AutorInteracao,
    Base,
    Campanha,
    Conexao,
    Conversa,
    Interacao,
    Lead,
    ModoIA,
    StatusConexao,
    StatusConversa,
    StatusInteracao,
    StatusLead,
    Usuario,
)


@pytest.fixture
def fabrica():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    yield fabrica
    Base.metadata.drop_all(engine)
    engine.dispose()


class _IAFake:
    def __init__(self, decisao=None, erro: str = ""):
        self.decisao = decisao
        self.erro = erro
        self.contextos = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def decidir(self, contexto):
        self.contextos.append(contexto)
        if self.erro:
            raise ErroGemini(self.erro)
        return self.decisao


class _EvolutionFake:
    def __init__(self):
        self.envios = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def enviar_texto(self, instancia, numero, texto):
        self.envios.append((instancia, numero, texto))
        return MensagemEnviada("wa-1", f"{numero}@s.whatsapp.net", numero)


def _decisao(**mudancas) -> DecisaoIA:
    dados = {
        "resposta": "Quem cuida dessa parte por ai?",
        "papel_contato": "atendente",
        "intencao": "buscar decisor",
        "etapa": "buscando_decisor",
        "precisa_humano": False,
        "encerrar": False,
        "confianca": 0.9,
        "resumo": "Atendente respondeu.",
    }
    dados.update(mudancas)
    return DecisaoIA(**dados)


def _cenario(fabrica, modo=ModoIA.RASCUNHO):
    with fabrica() as s:
        usuario = Usuario(email="dono@exemplo.com", senha_hash="x", nome="Dono")
        s.add(usuario)
        s.flush()
        conexao = Conexao(
            usuario_id=usuario.id,
            nome_instancia="dono-1",
            numero="5551998984086",
            status=StatusConexao.CONECTADA,
        )
        s.add(conexao)
        s.flush()
        campanha = Campanha(
            usuario_id=usuario.id,
            conexao_id=conexao.id,
            nome="Pets",
            modelos=["Oi"],
            modo_ia=modo,
            prompt_ia="Descobrir o dono.",
        )
        s.add(campanha)
        s.flush()
        lead = Lead(
            campanha_id=campanha.id,
            nome="Bicho Mania",
            telefone="5551998984086",
            categoria="Pet shop",
            status=StatusLead.RESPONDEU,
        )
        s.add(lead)
        s.commit()
        return lead.id, campanha.id


def _entrada(
    fabrica,
    lead_id,
    id_externo="entrada-1",
    texto="Sou atendente, do que se trata?",
):
    with fabrica() as s:
        lead = s.get(Lead, lead_id)
        return registrar_entrada(
            s, lead, texto=texto, id_externo=id_externo
        )


class TestRegistro:
    def test_webhook_repetido_reaproveita_a_mesma_interacao(self, fabrica):
        lead_id, _ = _cenario(fabrica)
        primeira = _entrada(fabrica, lead_id)
        segunda = _entrada(fabrica, lead_id)

        assert primeira.nova is True
        assert segunda.nova is False
        assert segunda.interacao_id == primeira.interacao_id

    def test_optout_ja_nasce_encerrado(self, fabrica):
        lead_id, _ = _cenario(fabrica)
        with fabrica() as s:
            lead = s.get(Lead, lead_id)
            lead.status = StatusLead.OPTOUT
            s.commit()
        entrada = _entrada(fabrica, lead_id)
        with fabrica() as s:
            conversa = s.get(Conversa, entrada.conversa_id)
            assert conversa.status == StatusConversa.ENCERRADA


class TestGerenciador:
    def test_modo_rascunho_nao_envia(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.RASCUNHO)
        entrada = _entrada(fabrica, lead_id)
        ia = _IAFake(_decisao())
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(fabrica, lambda: ia, lambda: evolution)

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "rascunho"
        assert evolution.envios == []
        with fabrica() as s:
            saida = s.get(Interacao, resultado.interacao_id)
            conversa = s.get(Conversa, entrada.conversa_id)
            assert saida.status == StatusInteracao.RASCUNHO
            assert saida.autor == AutorInteracao.IA
            assert conversa.status == StatusConversa.AGUARDANDO_HUMANO

    def test_modo_automatico_envia_uma_unica_vez(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.AUTOMATICA)
        entrada = _entrada(fabrica, lead_id)
        ia = _IAFake(_decisao())
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(fabrica, lambda: ia, lambda: evolution)

        primeiro = gerenciador.processar(entrada.interacao_id)
        segundo = gerenciador.processar(entrada.interacao_id)

        assert primeiro.acao == "enviada"
        assert segundo.acao == "duplicada"
        assert len(evolution.envios) == 1
        with fabrica() as s:
            conversa = s.get(Conversa, entrada.conversa_id)
            assert conversa.total_respostas_ia == 1

    def test_aprovar_rascunho_envia_e_reabre_conversa(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.RASCUNHO)
        entrada = _entrada(fabrica, lead_id)
        ia = _IAFake(_decisao())
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(fabrica, lambda: ia, lambda: evolution)
        rascunho = gerenciador.processar(entrada.interacao_id)

        resultado = gerenciador.aprovar(rascunho.interacao_id)

        assert resultado.acao == "enviada"
        assert len(evolution.envios) == 1
        with fabrica() as s:
            conversa = s.get(Conversa, entrada.conversa_id)
            assert conversa.status == StatusConversa.ABERTA

    def test_ia_desligada_so_registra(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.DESLIGADA)
        entrada = _entrada(fabrica, lead_id)
        ia = _IAFake(_decisao())
        gerenciador = GerenciadorConversa(fabrica, lambda: ia, lambda: _EvolutionFake())

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "desligada"
        assert ia.contextos == []

    def test_optout_nunca_chama_ia(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.AUTOMATICA)
        with fabrica() as s:
            lead = s.get(Lead, lead_id)
            lead.status = StatusLead.OPTOUT
            s.commit()
        entrada = _entrada(fabrica, lead_id)
        ia = _IAFake(_decisao())
        gerenciador = GerenciadorConversa(fabrica, lambda: ia, lambda: _EvolutionFake())

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "optout"
        assert ia.contextos == []

    def test_erro_do_gemini_fica_visivel_para_humano(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.AUTOMATICA)
        entrada = _entrada(fabrica, lead_id)
        gerenciador = GerenciadorConversa(
            fabrica, lambda: _IAFake(erro="sem cota"), lambda: _EvolutionFake()
        )

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "falhou"
        with fabrica() as s:
            conversa = s.get(Conversa, entrada.conversa_id)
            assert conversa.status == StatusConversa.ERRO
            assert "sem cota" in conversa.resumo

    def test_baixa_confianca_transfere_para_humano(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.AUTOMATICA)
        entrada = _entrada(fabrica, lead_id)
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(
            fabrica,
            lambda: _IAFake(_decisao(confianca=0.2)),
            lambda: evolution,
        )

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "rascunho"
        assert evolution.envios == []
        with fabrica() as s:
            conversa = s.get(Conversa, entrada.conversa_id)
            assert conversa.status == StatusConversa.AGUARDANDO_HUMANO

    def test_handoff_do_modelo_nao_envia_no_modo_automatico(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.AUTOMATICA)
        entrada = _entrada(fabrica, lead_id)
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(
            fabrica,
            lambda: _IAFake(_decisao(precisa_humano=True)),
            lambda: evolution,
        )

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "rascunho"
        assert evolution.envios == []

    def test_limite_de_respostas_para_antes_de_chamar_ia(self, fabrica):
        lead_id, campanha_id = _cenario(fabrica, ModoIA.AUTOMATICA)
        entrada = _entrada(fabrica, lead_id)
        with fabrica() as s:
            campanha = s.get(Campanha, campanha_id)
            conversa = s.get(Conversa, entrada.conversa_id)
            campanha.limite_respostas_ia = 1
            conversa.total_respostas_ia = 1
            s.commit()
        ia = _IAFake(_decisao())
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(
            fabrica, lambda: ia, lambda: evolution
        )

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "limite"
        assert ia.contextos == []
        assert evolution.envios == []
        with fabrica() as s:
            conversa = s.get(Conversa, entrada.conversa_id)
            assert conversa.status == StatusConversa.AGUARDANDO_HUMANO

    def test_pergunta_sobre_ia_recebe_resposta_transparente(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.AUTOMATICA)
        entrada = _entrada(
            fabrica,
            lead_id,
            texto="Voce e um robo?",
        )
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(
            fabrica,
            lambda: _IAFake(_decisao(resposta="Sou o Guilherme.")),
            lambda: evolution,
        )

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "enviada"
        assert evolution.envios[0][2] == RESPOSTA_TRANSPARENCIA
        assert "Guilherme" not in evolution.envios[0][2]

    def test_pedido_de_pessoa_interrompe_modo_automatico(self, fabrica):
        lead_id, _ = _cenario(fabrica, ModoIA.AUTOMATICA)
        entrada = _entrada(
            fabrica,
            lead_id,
            texto="Quero falar com uma pessoa de verdade.",
        )
        evolution = _EvolutionFake()
        gerenciador = GerenciadorConversa(
            fabrica,
            lambda: _IAFake(_decisao(precisa_humano=False)),
            lambda: evolution,
        )

        resultado = gerenciador.processar(entrada.interacao_id)

        assert resultado.acao == "rascunho"
        assert evolution.envios == []
        with fabrica() as s:
            conversa = s.get(Conversa, entrada.conversa_id)
            assert conversa.status == StatusConversa.AGUARDANDO_HUMANO
            assert conversa.resumo == "Contato pediu atendimento humano."
