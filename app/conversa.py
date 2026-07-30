"""Orquestracao de conversas: persistencia, Gemini, envio e handoff.

O webhook apenas registra a entrada e agenda `GerenciadorConversa.processar`.
Chamadas de rede acontecem fora da transacao do webhook. Cada resposta gerada
carrega `origem_interacao_id`, tornando o processamento idempotente.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol
import unicodedata

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.evolution import ErroEvolution, Evolution
from app.gemini import ContextoIA, DecisaoIA, ErroGemini
from app.models import (
    AutorInteracao,
    Campanha,
    Conexao,
    Conversa,
    EtapaConversa,
    Interacao,
    Lead,
    ModoIA,
    PapelContato,
    StatusConversa,
    StatusInteracao,
    StatusLead,
)

CONFIANCA_MINIMA_AUTOMATICA = 0.55
MAX_HISTORICO = 12
RESPOSTA_TRANSPARENCIA = (
    "Este atendimento usa automacao com IA. Se preferir, posso encaminhar "
    "a conversa para uma pessoa da equipe."
)


class ProvedorIA(Protocol):
    def __enter__(self) -> ProvedorIA: ...

    def __exit__(self, *_: object) -> None: ...

    def decidir(self, contexto: ContextoIA) -> DecisaoIA: ...


@dataclass(frozen=True)
class EntradaRegistrada:
    interacao_id: int
    conversa_id: int
    nova: bool


@dataclass(frozen=True)
class ResultadoConversa:
    acao: str
    conversa_id: int | None = None
    interacao_id: int | None = None
    motivo: str = ""


def registrar_entrada(
    sessao: Session,
    lead: Lead,
    *,
    texto: str,
    id_externo: str = "",
) -> EntradaRegistrada:
    """Persiste a fala antes de qualquer chamada a IA.

    Se a Evolution repetir o mesmo evento, devolve a interacao existente e
    `nova=False`; quem chamou nao agenda outro processamento.
    """
    id_externo = id_externo.strip()
    if id_externo:
        existente = sessao.scalar(
            select(Interacao).where(Interacao.id_externo == id_externo)
        )
        if existente is not None:
            return EntradaRegistrada(
                existente.id, existente.conversa_id, nova=False
            )

    conversa = sessao.scalar(
        select(Conversa).where(Conversa.lead_id == lead.id)
    )
    if conversa is None:
        conversa = Conversa(lead_id=lead.id)
        sessao.add(conversa)
        sessao.flush()

    if lead.status == StatusLead.OPTOUT:
        conversa.status = StatusConversa.ENCERRADA
        conversa.etapa = EtapaConversa.ENCERRADA
        conversa.resumo = "Contato pediu para nao receber novas mensagens."
    conversa.atualizada_em = datetime.now(timezone.utc)
    interacao = Interacao(
        conversa_id=conversa.id,
        autor=AutorInteracao.LEAD,
        status=StatusInteracao.RECEBIDA,
        texto=texto.strip()[:4000],
        id_externo=id_externo or None,
    )
    sessao.add(interacao)
    try:
        sessao.commit()
    except IntegrityError:
        # Corrida entre webhooks iguais. A unique de id_externo decide.
        sessao.rollback()
        if not id_externo:
            raise
        existente = sessao.scalar(
            select(Interacao).where(Interacao.id_externo == id_externo)
        )
        if existente is None:
            raise
        return EntradaRegistrada(existente.id, existente.conversa_id, nova=False)
    return EntradaRegistrada(interacao.id, conversa.id, nova=True)


class GerenciadorConversa:
    def __init__(
        self,
        fabrica_sessao: Callable[[], Session],
        fabrica_ia: Callable[[], ProvedorIA],
        fabrica_evolution: Callable[[], Evolution],
    ):
        self._fabrica_sessao = fabrica_sessao
        self._fabrica_ia = fabrica_ia
        self._fabrica_evolution = fabrica_evolution

    def processar(self, interacao_id: int) -> ResultadoConversa:
        """Gera rascunho ou envia uma unica resposta para uma entrada."""
        preparado = self._preparar(interacao_id)
        if isinstance(preparado, ResultadoConversa):
            return preparado
        contexto, conversa_id = preparado

        try:
            with self._fabrica_ia() as provedor:
                decisao = provedor.decidir(contexto)
        except ErroGemini as erro:
            return self._gravar_erro(interacao_id, conversa_id, erro.mensagem)
        except Exception:
            return self._gravar_erro(
                interacao_id,
                conversa_id,
                "Erro inesperado ao gerar a resposta.",
            )

        ultima_fala = contexto.historico[-1][1] if contexto.historico else ""
        if _perguntou_sobre_automacao(ultima_fala):
            decisao = replace(decisao, resposta=RESPOSTA_TRANSPARENCIA)
        if _pediu_atendimento_humano(ultima_fala):
            decisao = replace(
                decisao,
                precisa_humano=True,
                etapa=EtapaConversa.TRANSFERINDO.value,
                resumo="Contato pediu atendimento humano.",
            )

        return self._aplicar_decisao(interacao_id, decisao)

    def aprovar(self, interacao_id: int) -> ResultadoConversa:
        """Envia um rascunho da IA depois da aprovacao humana."""
        with self._fabrica_sessao() as sessao:
            interacao = sessao.get(Interacao, interacao_id)
            if (
                interacao is None
                or interacao.autor != AutorInteracao.IA
                or interacao.status != StatusInteracao.RASCUNHO
            ):
                return ResultadoConversa("ignorada", motivo="Rascunho nao encontrado.")
            conversa = sessao.get(Conversa, interacao.conversa_id)
            if conversa is None:
                return ResultadoConversa("ignorada", motivo="Conversa nao encontrada.")
            lead = sessao.get(Lead, conversa.lead_id)
            if lead is None:
                return ResultadoConversa("ignorada", motivo="Lead nao encontrado.")
            campanha = sessao.get(Campanha, lead.campanha_id)
            conexao = (
                sessao.get(Conexao, campanha.conexao_id)
                if campanha and campanha.conexao_id
                else None
            )
            if conexao is None or not lead.telefone:
                return self._falhar_envio(
                    sessao, interacao, conversa, "Conexao ou telefone indisponivel."
                )
            return self._enviar(
                sessao, interacao, conversa, conexao, lead.telefone, reabrir=True
            )

    def enviar_manual(self, conversa_id: int, texto: str) -> ResultadoConversa:
        """Resposta humana registrada na mesma timeline da IA."""
        texto = texto.strip()
        if not texto or len(texto) > 2000:
            return ResultadoConversa(
                "ignorada", conversa_id, motivo="Mensagem vazia ou longa demais."
            )
        with self._fabrica_sessao() as sessao:
            conversa = sessao.get(Conversa, conversa_id)
            lead = sessao.get(Lead, conversa.lead_id) if conversa else None
            campanha = sessao.get(Campanha, lead.campanha_id) if lead else None
            conexao = (
                sessao.get(Conexao, campanha.conexao_id)
                if campanha and campanha.conexao_id
                else None
            )
            if conversa is None or lead is None or conexao is None or not lead.telefone:
                return ResultadoConversa(
                    "falhou", conversa_id, motivo="Conversa sem conexao valida."
                )
            interacao = Interacao(
                conversa_id=conversa.id,
                autor=AutorInteracao.HUMANO,
                status=StatusInteracao.RASCUNHO,
                texto=texto,
            )
            sessao.add(interacao)
            sessao.flush()
            return self._enviar(
                sessao, interacao, conversa, conexao, lead.telefone, reabrir=False
            )

    def _preparar(
        self, interacao_id: int
    ) -> tuple[ContextoIA, int] | ResultadoConversa:
        with self._fabrica_sessao() as sessao:
            entrada = sessao.get(Interacao, interacao_id)
            if entrada is None or entrada.autor != AutorInteracao.LEAD:
                return ResultadoConversa("ignorada", motivo="Entrada nao encontrada.")
            ja_respondeu = sessao.scalar(
                select(Interacao).where(
                    Interacao.origem_interacao_id == entrada.id
                )
            )
            if ja_respondeu is not None:
                return ResultadoConversa(
                    "duplicada",
                    entrada.conversa_id,
                    ja_respondeu.id,
                    "Esta entrada ja foi processada.",
                )
            conversa = sessao.get(Conversa, entrada.conversa_id)
            lead = sessao.get(Lead, conversa.lead_id) if conversa else None
            campanha = sessao.get(Campanha, lead.campanha_id) if lead else None
            if conversa is None or lead is None or campanha is None:
                return ResultadoConversa("ignorada", motivo="Conversa incompleta.")
            if lead.status == StatusLead.OPTOUT:
                conversa.status = StatusConversa.ENCERRADA
                conversa.etapa = EtapaConversa.ENCERRADA
                sessao.commit()
                return ResultadoConversa("optout", conversa.id, motivo="Opt-out ativo.")
            if campanha.modo_ia == ModoIA.DESLIGADA:
                return ResultadoConversa(
                    "desligada", conversa.id, motivo="IA desligada na campanha."
                )
            if conversa.status != StatusConversa.ABERTA:
                return ResultadoConversa(
                    "aguardando_humano",
                    conversa.id,
                    motivo="Conversa nao esta liberada para automacao.",
                )
            if conversa.total_respostas_ia >= campanha.limite_respostas_ia:
                conversa.status = StatusConversa.AGUARDANDO_HUMANO
                conversa.etapa = EtapaConversa.TRANSFERINDO
                conversa.resumo = "Limite de respostas automaticas atingido."
                conversa.atualizada_em = datetime.now(timezone.utc)
                sessao.commit()
                return ResultadoConversa(
                    "limite", conversa.id, motivo=conversa.resumo
                )

            interacoes = sessao.scalars(
                select(Interacao)
                .where(
                    Interacao.conversa_id == conversa.id,
                    Interacao.status.in_(
                        (StatusInteracao.RECEBIDA, StatusInteracao.ENVIADA)
                    ),
                )
                .order_by(Interacao.id.desc())
                .limit(MAX_HISTORICO)
            ).all()
            interacoes.reverse()
            historico = tuple(
                (item.autor.value, item.texto) for item in interacoes
            )
            return (
                ContextoIA(
                    nome_lead=lead.nome,
                    categoria=lead.categoria or "",
                    objetivo=campanha.prompt_ia or "",
                    historico=historico,
                    case_real=campanha.case_ia,
                    link_agendamento=campanha.link_agendamento,
                ),
                conversa.id,
            )

    def _aplicar_decisao(
        self, entrada_id: int, decisao: DecisaoIA
    ) -> ResultadoConversa:
        with self._fabrica_sessao() as sessao:
            entrada = sessao.get(Interacao, entrada_id)
            if entrada is None:
                return ResultadoConversa("ignorada", motivo="Entrada desapareceu.")
            existente = sessao.scalar(
                select(Interacao).where(
                    Interacao.origem_interacao_id == entrada.id
                )
            )
            if existente is not None:
                return ResultadoConversa(
                    "duplicada", entrada.conversa_id, existente.id
                )
            conversa = sessao.get(Conversa, entrada.conversa_id)
            lead = sessao.get(Lead, conversa.lead_id) if conversa else None
            campanha = sessao.get(Campanha, lead.campanha_id) if lead else None
            if conversa is None or lead is None or campanha is None:
                return ResultadoConversa("ignorada", motivo="Conversa incompleta.")

            conversa.papel_contato = PapelContato(decisao.papel_contato)
            conversa.etapa = EtapaConversa(decisao.etapa)
            conversa.resumo = decisao.resumo or decisao.intencao
            conversa.atualizada_em = datetime.now(timezone.utc)

            baixa_confianca = decisao.confianca < CONFIANCA_MINIMA_AUTOMATICA
            precisa_humano = decisao.precisa_humano or baixa_confianca
            if decisao.encerrar:
                conversa.status = StatusConversa.ENCERRADA
                conversa.etapa = EtapaConversa.ENCERRADA
            elif precisa_humano or campanha.modo_ia == ModoIA.RASCUNHO:
                conversa.status = StatusConversa.AGUARDANDO_HUMANO

            if not decisao.resposta:
                if conversa.status == StatusConversa.ABERTA:
                    conversa.status = StatusConversa.AGUARDANDO_HUMANO
                sessao.commit()
                return ResultadoConversa(
                    "sem_resposta", conversa.id, motivo="IA pediu intervencao humana."
                )

            saida = Interacao(
                conversa_id=conversa.id,
                origem_interacao_id=entrada.id,
                autor=AutorInteracao.IA,
                status=StatusInteracao.RASCUNHO,
                texto=decisao.resposta,
            )
            sessao.add(saida)
            try:
                sessao.flush()
            except IntegrityError:
                sessao.rollback()
                existente = sessao.scalar(
                    select(Interacao).where(
                        Interacao.origem_interacao_id == entrada.id
                    )
                )
                return ResultadoConversa(
                    "duplicada",
                    conversa.id,
                    existente.id if existente else None,
                )

            # Mesmo no modo automatico, baixa confianca e handoff explicito
            # precisam de revisao humana antes de qualquer envio.
            if campanha.modo_ia == ModoIA.RASCUNHO or precisa_humano:
                sessao.commit()
                return ResultadoConversa("rascunho", conversa.id, saida.id)

            conexao = (
                sessao.get(Conexao, campanha.conexao_id)
                if campanha.conexao_id
                else None
            )
            if conexao is None or not lead.telefone:
                return self._falhar_envio(
                    sessao, saida, conversa, "Conexao ou telefone indisponivel."
                )
            return self._enviar(
                sessao, saida, conversa, conexao, lead.telefone, reabrir=False
            )

    def _enviar(
        self,
        sessao: Session,
        interacao: Interacao,
        conversa: Conversa,
        conexao: Conexao,
        telefone: str,
        *,
        reabrir: bool,
    ) -> ResultadoConversa:
        try:
            with self._fabrica_evolution() as evolution:
                enviada = evolution.enviar_texto(
                    conexao.nome_instancia, telefone, interacao.texto
                )
        except ErroEvolution as erro:
            return self._falhar_envio(
                sessao, interacao, conversa, erro.mensagem
            )
        except Exception:
            return self._falhar_envio(
                sessao, interacao, conversa, "Erro inesperado ao enviar resposta."
            )

        interacao.status = StatusInteracao.ENVIADA
        interacao.id_externo = enviada.id_mensagem or None
        if interacao.autor == AutorInteracao.IA:
            conversa.total_respostas_ia += 1
        if reabrir and conversa.etapa not in {
            EtapaConversa.TRANSFERINDO,
            EtapaConversa.ENCERRADA,
        }:
            conversa.status = StatusConversa.ABERTA
        conversa.atualizada_em = datetime.now(timezone.utc)
        sessao.commit()
        return ResultadoConversa("enviada", conversa.id, interacao.id)

    @staticmethod
    def _falhar_envio(
        sessao: Session,
        interacao: Interacao,
        conversa: Conversa,
        motivo: str,
    ) -> ResultadoConversa:
        interacao.status = StatusInteracao.FALHOU
        interacao.erro = motivo[:1000]
        conversa.status = StatusConversa.ERRO
        conversa.resumo = motivo[:1000]
        conversa.atualizada_em = datetime.now(timezone.utc)
        sessao.commit()
        return ResultadoConversa("falhou", conversa.id, interacao.id, motivo)

    def _gravar_erro(
        self, entrada_id: int, conversa_id: int, motivo: str
    ) -> ResultadoConversa:
        with self._fabrica_sessao() as sessao:
            conversa = sessao.get(Conversa, conversa_id)
            if conversa is None:
                return ResultadoConversa("falhou", motivo=motivo)
            existente = sessao.scalar(
                select(Interacao).where(
                    Interacao.origem_interacao_id == entrada_id
                )
            )
            if existente is None:
                existente = Interacao(
                    conversa_id=conversa.id,
                    origem_interacao_id=entrada_id,
                    autor=AutorInteracao.IA,
                    status=StatusInteracao.FALHOU,
                    texto="",
                    erro=motivo[:1000],
                )
                sessao.add(existente)
            conversa.status = StatusConversa.ERRO
            conversa.resumo = motivo[:1000]
            conversa.atualizada_em = datetime.now(timezone.utc)
            sessao.commit()
            return ResultadoConversa(
                "falhou", conversa.id, existente.id, motivo
            )


def _normalizar_intencao(texto: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto.casefold())
        if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.split())


def _perguntou_sobre_automacao(texto: str) -> bool:
    texto_n = _normalizar_intencao(texto)
    expressoes = (
        "voce e robo",
        "voce e um robo",
        "voce e bot",
        "voce e uma ia",
        "isso e ia",
        "e inteligencia artificial",
        "mensagem automatica",
        "atendimento automatico",
        "voce e humano",
        "voce e uma pessoa",
    )
    return any(expressao in texto_n for expressao in expressoes)


def _pediu_atendimento_humano(texto: str) -> bool:
    texto_n = _normalizar_intencao(texto)
    expressoes = (
        "falar com uma pessoa",
        "falar com pessoa",
        "falar com atendente",
        "atendimento humano",
        "quero um humano",
        "quero falar com humano",
        "pessoa de verdade",
    )
    return any(expressao in texto_n for expressao in expressoes)


__all__ = [
    "CONFIANCA_MINIMA_AUTOMATICA",
    "EntradaRegistrada",
    "GerenciadorConversa",
    "RESPOSTA_TRANSPARENCIA",
    "ResultadoConversa",
    "registrar_entrada",
]
