"""Motor de disparo: o laço que respeita ritmo, opt-out e ja-contatado.

A Evolution API nao tem fila nem rate limit (issue #2538 fechada como not
planned). Este modulo e o que impede o numero do usuario de morrer numa
rajada: antes de CADA envio consulta `ritmo.avaliar`, e entre envios dorme
`ritmo.proximo_intervalo`.

Regras que nao negociam:

1. **Nunca reenvia.** Lead em ENVIADO/FALHOU/SEM_WHATSAPP/OPTOUT/RESPONDEU
   sai da fila. Falha de rede grava FALHOU e segue para o proximo — timeout
   pode ter entregado a mensagem do outro lado.
2. **OptOut e JaContatado sao globais por usuario.** Valem entre campanhas.
3. **Freio permanente pausa a campanha** e grava `motivo_pausa`. Nao e espera:
   e "pare e reveja o texto".
4. **Checagem de WhatsApp testa variantes do nono digito** antes de mandar.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import mensagem as mod_mensagem
from app import ritmo
from app import telefone as mod_telefone
from app.evolution import (
    Evolution,
    ErroEvolution,
    NumeroInvalido,
    ServicoIndisponivel,
)
from app.models import (
    ENTREGUES,
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
)

logger = logging.getLogger(__name__)

# Quanto esperar quando o ritmo recusou mas a campanha continua rodando
# (teto horario, fora da janela). Curto o bastante para retomar cedo; longo o
# bastante para nao martelar o banco.
ESPERA_QUANDO_BLOQUEADO_SEG = 30

# Frases que contam como "pare de me mandar mensagem". Comparacao em minusculas,
# com palavra inteira onde faz sentido ("para" sozinho, nao "paraiso").
_PEDIDOS_OPTOUT = (
    r"\bpara\b",
    r"\bparar\b",
    r"\bsair\b",
    r"\bstop\b",
    r"\bcancelar\b",
    r"\bremover\b",
    r"\bn[aã]o tenho interesse\b",
    r"\bn[aã]o quero\b",
    r"\bme tira\b",
    r"\bnao me envie\b",
    r"\bn[aã]o me mande\b",
)
_RE_OPTOUT = re.compile("|".join(_PEDIDOS_OPTOUT), re.IGNORECASE)


@dataclass(frozen=True)
class ResultadoPasso:
    """O que um ciclo do laço fez — para log, teste e a tela de progresso."""

    acao: str
    lead_id: int | None = None
    motivo: str = ""
    espera_seg: int = 0


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def e_pedido_optout(texto: str) -> bool:
    """True se a mensagem do lead pede para parar."""
    return bool(texto and _RE_OPTOUT.search(texto.strip()))


def montar_situacao(
    sessao: Session,
    conexao: Conexao,
    agora: datetime | None = None,
) -> ritmo.Situacao:
    """Contadores que o freio e o teto leem — por CONEXAO, nao por campanha.

    Duas campanhas no mesmo numero somam: o ban e o teto diario sao do chip.
    """
    agora = agora or agora_utc()
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=timezone.utc)

    inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_hora = agora - timedelta(hours=1)

    campanha_ids = select(Campanha.id).where(Campanha.conexao_id == conexao.id)

    enviadas_hoje = (
        sessao.scalar(
            select(func.count())
            .select_from(Mensagem)
            .where(
                Mensagem.campanha_id.in_(campanha_ids),
                Mensagem.enviada_em >= inicio_dia,
                Mensagem.status_entrega != StatusEntrega.FALHOU,
            )
        )
        or 0
    )
    enviadas_ultima_hora = (
        sessao.scalar(
            select(func.count())
            .select_from(Mensagem)
            .where(
                Mensagem.campanha_id.in_(campanha_ids),
                Mensagem.enviada_em >= inicio_hora,
                Mensagem.status_entrega != StatusEntrega.FALHOU,
            )
        )
        or 0
    )
    total_entregues = (
        sessao.scalar(
            select(func.count())
            .select_from(Mensagem)
            .where(
                Mensagem.campanha_id.in_(campanha_ids),
                Mensagem.status_entrega.in_(ENTREGUES),
            )
        )
        or 0
    )
    total_bloqueios = (
        sessao.scalar(
            select(func.count())
            .select_from(Mensagem)
            .where(
                Mensagem.campanha_id.in_(campanha_ids),
                Mensagem.status_entrega == StatusEntrega.BLOQUEADA,
            )
        )
        or 0
    )
    # Respostas: leads das campanhas desta conexao que responderam.
    total_respostas = (
        sessao.scalar(
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.campanha_id.in_(campanha_ids),
                Lead.status == StatusLead.RESPONDEU,
            )
        )
        or 0
    )

    return ritmo.Situacao(
        idade_conexao_dias=conexao.idade_dias,
        enviadas_hoje=int(enviadas_hoje),
        enviadas_ultima_hora=int(enviadas_ultima_hora),
        total_entregues=int(total_entregues),
        total_respostas=int(total_respostas),
        total_bloqueios=int(total_bloqueios),
    )


def proximo_lead_pendente(sessao: Session, campanha: Campanha) -> Lead | None:
    """Proximo lead ainda na fila (PENDENTE), em ordem estavel de id."""
    return sessao.scalar(
        select(Lead)
        .where(
            Lead.campanha_id == campanha.id,
            Lead.status == StatusLead.PENDENTE,
        )
        .order_by(Lead.id)
        .limit(1)
    )


def em_optout(sessao: Session, usuario_id: int, telefone: str) -> bool:
    return (
        sessao.scalar(
            select(OptOut.id).where(
                OptOut.usuario_id == usuario_id,
                OptOut.telefone == telefone,
            )
        )
        is not None
    )


def em_ja_contatado(sessao: Session, usuario_id: int, telefone: str) -> bool:
    return (
        sessao.scalar(
            select(JaContatado.id).where(
                JaContatado.usuario_id == usuario_id,
                JaContatado.telefone == telefone,
            )
        )
        is not None
    )


def registrar_optout(
    sessao: Session,
    usuario_id: int,
    telefone: str,
    motivo: str | None = None,
) -> OptOut:
    """Grava opt-out se ainda nao existir. Idempotente."""
    existente = sessao.scalar(
        select(OptOut).where(
            OptOut.usuario_id == usuario_id,
            OptOut.telefone == telefone,
        )
    )
    if existente is not None:
        return existente
    registro = OptOut(usuario_id=usuario_id, telefone=telefone, motivo=motivo)
    sessao.add(registro)
    sessao.flush()
    return registro


def registrar_ja_contatado(
    sessao: Session,
    usuario_id: int,
    telefone: str,
    campanha_id: int | None,
) -> None:
    if em_ja_contatado(sessao, usuario_id, telefone):
        return
    sessao.add(
        JaContatado(
            usuario_id=usuario_id,
            telefone=telefone,
            campanha_id=campanha_id,
        )
    )


def checar_whatsapp(
    evolution: Evolution,
    nome_instancia: str,
    telefone_e164: str,
) -> str | None:
    """Devolve o numero canonico com WhatsApp, ou None.

    Testa as variantes do nono digito. Prefere a que a Evolution confirma.
    """
    tel = mod_telefone.normalizar(telefone_e164)
    candidatos = mod_telefone.variantes(tel) or [telefone_e164]
    try:
        resultados = evolution.checar_numeros(nome_instancia, candidatos)
    except ErroEvolution:
        raise

    for r in resultados:
        if r.existe:
            return r.numero or r.consultado
    return None


def processar_proximo(
    sessao: Session,
    campanha: Campanha,
    evolution: Evolution,
    *,
    agora: datetime | None = None,
    sorteio: random.Random | None = None,
) -> ResultadoPasso:
    """Um ciclo do laço: avalia ritmo e, se liberado, tenta o proximo lead.

    Quem chama decide dormir `espera_seg` e se continua o laço. Esta funcao
    nao dorme — assim o teste e deterministico.
    """
    agora = agora or agora_utc()
    sessao.refresh(campanha)

    if campanha.status != StatusCampanha.RODANDO:
        return ResultadoPasso(acao="parada", motivo=f"status={campanha.status.value}")

    if not campanha.modelos:
        return _pausar(sessao, campanha, "Campanha sem modelos de mensagem.")

    try:
        mod_mensagem.validar(campanha.modelos)
    except mod_mensagem.ModeloInvalido as erro:
        return _pausar(sessao, campanha, str(erro))

    if campanha.conexao_id is None:
        return _pausar(sessao, campanha, "Campanha sem conexao de WhatsApp.")

    conexao = sessao.get(Conexao, campanha.conexao_id)
    if conexao is None or conexao.status != StatusConexao.CONECTADA:
        return _pausar(
            sessao,
            campanha,
            "WhatsApp desconectado. Reconecte pelo QR antes de retomar.",
        )

    situacao = montar_situacao(sessao, conexao, agora)
    decisao = ritmo.avaliar(campanha.perfil(), situacao, agora)

    if decisao.freio_permanente:
        return _pausar(sessao, campanha, decisao.motivo)

    if not decisao.liberado:
        return ResultadoPasso(
            acao="aguardando",
            motivo=decisao.motivo,
            espera_seg=ESPERA_QUANDO_BLOQUEADO_SEG,
        )

    lead = proximo_lead_pendente(sessao, campanha)
    if lead is None:
        campanha.status = StatusCampanha.CONCLUIDA
        campanha.motivo_pausa = None
        sessao.commit()
        return ResultadoPasso(acao="concluida", motivo="Fila vazia.")

    if not lead.telefone:
        lead.status = StatusLead.FALHOU
        sessao.commit()
        return ResultadoPasso(
            acao="falhou",
            lead_id=lead.id,
            motivo="Lead sem telefone.",
        )

    if em_optout(sessao, campanha.usuario_id, lead.telefone):
        lead.status = StatusLead.OPTOUT
        sessao.commit()
        return ResultadoPasso(
            acao="optout",
            lead_id=lead.id,
            motivo="Telefone na lista de opt-out.",
        )

    if em_ja_contatado(sessao, campanha.usuario_id, lead.telefone):
        # Ja abordado noutra campanha: fecha a fila sem reenviar.
        lead.status = StatusLead.FALHOU
        sessao.commit()
        return ResultadoPasso(
            acao="ja_contatado",
            lead_id=lead.id,
            motivo="Telefone ja contatado em outra campanha.",
        )

    lead.status = StatusLead.CHECANDO
    sessao.commit()

    try:
        numero_wa = checar_whatsapp(
            evolution, conexao.nome_instancia, lead.telefone
        )
    except ErroEvolution as erro:
        # Checagem falhou por rede: devolve para PENDENTE para tentar de novo
        # depois — diferente de "nao tem WhatsApp".
        lead.status = StatusLead.PENDENTE
        sessao.commit()
        return ResultadoPasso(
            acao="aguardando",
            lead_id=lead.id,
            motivo=f"Falha ao checar WhatsApp: {erro.mensagem}",
            espera_seg=ESPERA_QUANDO_BLOQUEADO_SEG,
        )

    if not numero_wa:
        lead.status = StatusLead.SEM_WHATSAPP
        sessao.commit()
        return ResultadoPasso(
            acao="sem_whatsapp",
            lead_id=lead.id,
            motivo="Numero sem WhatsApp (incl. variantes do nono digito).",
        )

    # Atualiza para o canonico que a Evolution resolveu (nono digito).
    if numero_wa != lead.telefone:
        lead.telefone = numero_wa

    texto = mod_mensagem.montar_para(campanha.modelos, lead, sorteio)

    try:
        enviada = evolution.enviar_texto(
            conexao.nome_instancia, numero_wa, texto
        )
    except NumeroInvalido as erro:
        lead.status = StatusLead.SEM_WHATSAPP
        sessao.add(
            Mensagem(
                lead_id=lead.id,
                campanha_id=campanha.id,
                texto=texto,
                status_entrega=StatusEntrega.FALHOU,
                erro=erro.mensagem,
            )
        )
        sessao.commit()
        return ResultadoPasso(
            acao="sem_whatsapp",
            lead_id=lead.id,
            motivo=erro.mensagem,
        )
    except (ServicoIndisponivel, ErroEvolution) as erro:
        # NAO reenvia. Grava falha e segue. Timeout pode ter entregado.
        lead.status = StatusLead.FALHOU
        sessao.add(
            Mensagem(
                lead_id=lead.id,
                campanha_id=campanha.id,
                texto=texto,
                status_entrega=StatusEntrega.FALHOU,
                erro=erro.mensagem,
            )
        )
        sessao.commit()
        return ResultadoPasso(
            acao="falhou",
            lead_id=lead.id,
            motivo=erro.mensagem,
            espera_seg=ritmo.proximo_intervalo(campanha.perfil(), sorteio),
        )

    lead.status = StatusLead.ENVIADO
    sessao.add(
        Mensagem(
            lead_id=lead.id,
            campanha_id=campanha.id,
            texto=texto,
            status_entrega=StatusEntrega.ENVIADA,
            id_externo=enviada.id_mensagem or None,
        )
    )
    registrar_ja_contatado(
        sessao, campanha.usuario_id, numero_wa, campanha.id
    )
    sessao.commit()

    espera = ritmo.proximo_intervalo(campanha.perfil(), sorteio)
    return ResultadoPasso(
        acao="enviado",
        lead_id=lead.id,
        motivo=f"Enviado para {numero_wa}.",
        espera_seg=espera,
    )


def _pausar(sessao: Session, campanha: Campanha, motivo: str) -> ResultadoPasso:
    campanha.status = StatusCampanha.PAUSADA
    campanha.motivo_pausa = motivo
    sessao.commit()
    return ResultadoPasso(acao="pausada", motivo=motivo)


def pode_iniciar(sessao: Session, campanha: Campanha) -> tuple[bool, str]:
    """Validacao da tela "Iniciar" — devolve (ok, motivo_se_nao)."""
    if campanha.status == StatusCampanha.RODANDO:
        return False, "Campanha ja esta em execucao."
    if campanha.status == StatusCampanha.CONCLUIDA:
        pendentes = proximo_lead_pendente(sessao, campanha)
        if pendentes is None:
            return False, "Campanha ja concluida e sem leads pendentes."
    if not campanha.modelos:
        return False, "Salve pelo menos um modelo de mensagem."
    try:
        mod_mensagem.validar(campanha.modelos)
    except mod_mensagem.ModeloInvalido as erro:
        return False, str(erro)
    if campanha.conexao_id is None:
        return False, "Escolha uma conexao de WhatsApp para a campanha."
    conexao = sessao.get(Conexao, campanha.conexao_id)
    if conexao is None or conexao.status != StatusConexao.CONECTADA:
        return False, "WhatsApp desconectado. Escaneie o QR antes de disparar."
    if proximo_lead_pendente(sessao, campanha) is None:
        return False, "Nao ha leads pendentes nesta campanha."
    return True, ""


def progresso(sessao: Session, campanha: Campanha) -> dict:
    """Numeros para a tela de acompanhamento ao vivo."""
    rows = sessao.execute(
        select(Lead.status, func.count())
        .where(Lead.campanha_id == campanha.id)
        .group_by(Lead.status)
    ).all()
    por = {st.value if hasattr(st, "value") else str(st): int(n) for st, n in rows}
    total = sum(por.values())
    pendentes = por.get(StatusLead.PENDENTE.value, 0) + por.get(
        StatusLead.CHECANDO.value, 0
    )
    return {
        "campanha_id": campanha.id,
        "status": campanha.status.value,
        "motivo_pausa": campanha.motivo_pausa,
        "total": total,
        "pendentes": pendentes,
        "enviados": por.get(StatusLead.ENVIADO.value, 0),
        "respondeu": por.get(StatusLead.RESPONDEU.value, 0),
        "sem_whatsapp": por.get(StatusLead.SEM_WHATSAPP.value, 0),
        "falhou": por.get(StatusLead.FALHOU.value, 0),
        "optout": por.get(StatusLead.OPTOUT.value, 0),
        "por_status": por,
    }


# ---------------------------------------------------------------------------
# Resposta / webhook
# ---------------------------------------------------------------------------


def processar_resposta_recebida(
    sessao: Session,
    *,
    nome_instancia: str,
    numero: str,
    texto: str,
) -> Lead | None:
    """Marca lead como respondeu e, se for o caso, grava opt-out.

    Procura o lead ENVIADO mais recente daquele telefone nas campanhas do
    usuario dono da instancia. Devolve o lead tocado, ou None.
    """
    numero = re.sub(r"\D", "", numero or "")
    if not numero:
        return None

    conexao = sessao.scalar(
        select(Conexao).where(Conexao.nome_instancia == nome_instancia)
    )
    if conexao is None:
        return None

    # Aceita o numero com ou sem o nono digito na comparacao.
    tel = mod_telefone.normalizar(numero)
    candidatos = set(mod_telefone.variantes(tel) or [numero])
    candidatos.add(numero)

    lead = sessao.scalar(
        select(Lead)
        .join(Campanha, Lead.campanha_id == Campanha.id)
        .where(
            Campanha.usuario_id == conexao.usuario_id,
            Lead.telefone.in_(candidatos),
            Lead.status.in_(
                (
                    StatusLead.ENVIADO,
                    StatusLead.RESPONDEU,
                    StatusLead.CHECANDO,
                    StatusLead.PENDENTE,
                )
            ),
        )
        .order_by(Lead.id.desc())
        .limit(1)
    )
    if lead is None:
        return None

    if e_pedido_optout(texto):
        registrar_optout(
            sessao,
            conexao.usuario_id,
            lead.telefone or numero,
            motivo=texto[:500],
        )
        lead.status = StatusLead.OPTOUT
    else:
        if lead.status != StatusLead.OPTOUT:
            lead.status = StatusLead.RESPONDEU

    # Mensagens entregues que alimentam o denominador do freio: se ainda
    # estavam so em "enviada", sobem para "entregue" — alguem respondeu,
    # entao chegou.
    msgs = sessao.scalars(
        select(Mensagem).where(
            Mensagem.lead_id == lead.id,
            Mensagem.status_entrega.in_(
                (StatusEntrega.ENVIADA, StatusEntrega.PENDENTE)
            ),
        )
    ).all()
    for m in msgs:
        m.status_entrega = StatusEntrega.ENTREGUE

    sessao.commit()
    return lead


def atualizar_entrega_por_id_externo(
    sessao: Session,
    id_externo: str,
    status: StatusEntrega,
) -> Mensagem | None:
    if not id_externo:
        return None
    msg = sessao.scalar(
        select(Mensagem).where(Mensagem.id_externo == id_externo).limit(1)
    )
    if msg is None:
        return None
    msg.status_entrega = status
    if status == StatusEntrega.BLOQUEADA and msg.lead_id:
        # Bloqueio e o sinal mais forte do freio de reputacao.
        pass
    sessao.commit()
    return msg


# ---------------------------------------------------------------------------
# Worker em thread (um por campanha)
# ---------------------------------------------------------------------------


class GerenciadorDisparo:
    """Sobe um thread por campanha rodando. Idempotente em `iniciar`."""

    def __init__(
        self,
        fabrica_sessao: Callable[[], Session],
        fabrica_evolution: Callable[[], Evolution],
        *,
        dormir: Callable[[float], None] = time.sleep,
        relogio: Callable[[], datetime] = agora_utc,
    ):
        self._fabrica_sessao = fabrica_sessao
        self._fabrica_evolution = fabrica_evolution
        self._dormir = dormir
        self._relogio = relogio
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}

    def esta_rodando(self, campanha_id: int) -> bool:
        with self._lock:
            t = self._threads.get(campanha_id)
            return bool(t and t.is_alive())

    def iniciar(self, campanha_id: int) -> bool:
        """Sobe o worker se ainda nao houver. Devolve True se (re)iniciou."""
        with self._lock:
            atual = self._threads.get(campanha_id)
            if atual is not None and atual.is_alive():
                return False
            t = threading.Thread(
                target=self._rodar,
                args=(campanha_id,),
                name=f"disparo-{campanha_id}",
                daemon=True,
            )
            self._threads[campanha_id] = t
            t.start()
            return True

    def _rodar(self, campanha_id: int) -> None:
        logger.info("worker de disparo iniciado campanha=%s", campanha_id)
        try:
            while True:
                with self._fabrica_sessao() as sessao:
                    campanha = sessao.get(Campanha, campanha_id)
                    if campanha is None:
                        return
                    if campanha.status != StatusCampanha.RODANDO:
                        return

                    try:
                        with self._fabrica_evolution() as evo:
                            resultado = processar_proximo(
                                sessao,
                                campanha,
                                evo,
                                agora=self._relogio(),
                            )
                    except Exception:
                        logger.exception(
                            "erro inesperado no disparo campanha=%s", campanha_id
                        )
                        campanha = sessao.get(Campanha, campanha_id)
                        if campanha is not None:
                            campanha.status = StatusCampanha.PAUSADA
                            campanha.motivo_pausa = (
                                "Erro interno no motor de disparo. Veja o log."
                            )
                            sessao.commit()
                        return

                if resultado.acao in {"pausada", "concluida", "parada"}:
                    logger.info(
                        "worker encerrou campanha=%s acao=%s motivo=%s",
                        campanha_id,
                        resultado.acao,
                        resultado.motivo,
                    )
                    return

                espera = resultado.espera_seg or 0
                if espera > 0:
                    self._dormir(espera)
                else:
                    # Evita busy-loop se um passo nao pediu espera (skip).
                    self._dormir(0.05)
        finally:
            with self._lock:
                self._threads.pop(campanha_id, None)
            logger.info("worker de disparo finalizado campanha=%s", campanha_id)
