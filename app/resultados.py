"""Consultas de produto: funil geral e comparacao das variacoes."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Campanha,
    Conversa,
    EtapaConversa,
    Lead,
    Mensagem,
    PapelContato,
    StatusConversa,
    StatusEntrega,
    StatusLead,
)


@dataclass(frozen=True)
class ResultadoVariacao:
    indice: int
    texto: str
    tentativas: int
    enviadas: int
    entregues: int
    respostas: int
    decisores: int
    transferencias: int
    optouts: int
    bloqueios: int
    falhas: int

    @property
    def taxa_resposta(self) -> float:
        return round(self.respostas * 100 / self.enviadas, 1) if self.enviadas else 0.0

    @property
    def taxa_decisor(self) -> float:
        return round(self.decisores * 100 / self.enviadas, 1) if self.enviadas else 0.0


@dataclass(frozen=True)
class ResumoUsuario:
    campanhas: int
    leads: int
    enviadas: int
    respostas: int
    decisores: int
    optouts: int
    aguardando_humano: int

    @property
    def taxa_resposta(self) -> float:
        return round(self.respostas * 100 / self.enviadas, 1) if self.enviadas else 0.0


def variantes_da_campanha(
    sessao: Session, campanha_id: int
) -> list[ResultadoVariacao]:
    """Agrupa pelo snapshot do modelo, nao pelo texto preenchido do lead."""
    rows = sessao.execute(
        select(Mensagem, Lead.status, Conversa.papel_contato, Conversa.etapa)
        .join(Lead, Mensagem.lead_id == Lead.id)
        .outerjoin(Conversa, Conversa.lead_id == Lead.id)
        .where(
            Mensagem.campanha_id == campanha_id,
            Mensagem.variante_texto.is_not(None),
        )
        .order_by(Mensagem.variante_indice, Mensagem.id)
    ).all()
    grupos: dict[tuple[int, str], dict[str, int]] = {}
    for mensagem, status_lead, papel, etapa in rows:
        chave = (mensagem.variante_indice or 0, mensagem.variante_texto or "")
        g = grupos.setdefault(
            chave,
            {
                "tentativas": 0,
                "enviadas": 0,
                "entregues": 0,
                "respostas": 0,
                "decisores": 0,
                "transferencias": 0,
                "optouts": 0,
                "bloqueios": 0,
                "falhas": 0,
            },
        )
        g["tentativas"] += 1
        if mensagem.status_entrega == StatusEntrega.FALHOU:
            g["falhas"] += 1
        else:
            g["enviadas"] += 1
        if mensagem.status_entrega in {
            StatusEntrega.ENTREGUE,
            StatusEntrega.LIDA,
            StatusEntrega.BLOQUEADA,
        }:
            g["entregues"] += 1
        if mensagem.status_entrega == StatusEntrega.BLOQUEADA:
            g["bloqueios"] += 1
        if status_lead in {StatusLead.RESPONDEU, StatusLead.OPTOUT}:
            g["respostas"] += 1
        if status_lead == StatusLead.OPTOUT:
            g["optouts"] += 1
        if papel == PapelContato.DECISOR:
            g["decisores"] += 1
        if etapa == EtapaConversa.TRANSFERINDO:
            g["transferencias"] += 1

    return [
        ResultadoVariacao(indice=indice, texto=texto, **contadores)
        for (indice, texto), contadores in sorted(grupos.items())
    ]


def resumo_usuario(sessao: Session, usuario_id: int) -> ResumoUsuario:
    campanha_ids = select(Campanha.id).where(Campanha.usuario_id == usuario_id)
    campanhas = (
        sessao.scalar(
            select(func.count()).select_from(Campanha).where(Campanha.usuario_id == usuario_id)
        )
        or 0
    )
    por_lead = dict(
        sessao.execute(
            select(Lead.status, func.count())
            .where(Lead.campanha_id.in_(campanha_ids))
            .group_by(Lead.status)
        ).all()
    )
    enviadas = (
        sessao.scalar(
            select(func.count())
            .select_from(Mensagem)
            .where(
                Mensagem.campanha_id.in_(campanha_ids),
                Mensagem.status_entrega != StatusEntrega.FALHOU,
            )
        )
        or 0
    )
    decisores = (
        sessao.scalar(
            select(func.count())
            .select_from(Conversa)
            .join(Lead, Conversa.lead_id == Lead.id)
            .where(
                Lead.campanha_id.in_(campanha_ids),
                Conversa.papel_contato == PapelContato.DECISOR,
            )
        )
        or 0
    )
    aguardando = (
        sessao.scalar(
            select(func.count())
            .select_from(Conversa)
            .join(Lead, Conversa.lead_id == Lead.id)
            .where(
                Lead.campanha_id.in_(campanha_ids),
                Conversa.status.in_(
                    (StatusConversa.AGUARDANDO_HUMANO, StatusConversa.ERRO)
                ),
            )
        )
        or 0
    )
    respostas = por_lead.get(StatusLead.RESPONDEU, 0) + por_lead.get(
        StatusLead.OPTOUT, 0
    )
    return ResumoUsuario(
        campanhas=int(campanhas),
        leads=sum(int(n) for n in por_lead.values()),
        enviadas=int(enviadas),
        respostas=int(respostas),
        decisores=int(decisores),
        optouts=int(por_lead.get(StatusLead.OPTOUT, 0)),
        aguardando_humano=int(aguardando),
    )


__all__ = ["ResultadoVariacao", "ResumoUsuario", "resumo_usuario", "variantes_da_campanha"]
