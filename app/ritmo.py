"""Politica de ritmo: quanto pode sair, quando, e com que intervalo.

Este modulo e o coracao do anti-ban, e existe porque o Evolution API nao tem
rate limiter, fila nem retry - a issue #2538 do projeto pede exatamente isso e
foi fechada como "not planned".

Os numeros vem da pesquisa de campo (ver README):

  - teto de 30 mensagens/hora; acima de 60/h dispara fiscalizacao
  - ~1 mensagem por minuto em disparo, com intervalo ALEATORIO
    (intervalo fixo e assinatura de robo)
  - aquecimento em 14 dias: 20-50/dia no inicio, ate 200/dia numa conta madura
  - volume concentrado em ~8h uteis parece humano; espalhado em 24h nao

Tudo aqui e funcao pura: recebe estado, devolve decisao. Sem banco, sem rede,
sem relogio global - a hora entra como parametro para poder ser testada.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import datetime, time

# Degraus de aquecimento: (dia_final, teto_diario). O primeiro degrau cujo
# dia_final >= idade da conexao vence.
DEGRAUS_AQUECIMENTO: tuple[tuple[int, int], ...] = (
    (3, 50),
    (7, 100),
    (14, 200),
)
TETO_CONTA_MADURA = 200

# Limites que a pesquisa aponta como zona de risco.
TETO_HORA_SEGURO = 30
TETO_HORA_CRITICO = 60
BLOQUEIO_MAXIMO = 0.02
RESPOSTA_MINIMA = 0.15
# Abaixo disso a taxa ainda nao diz nada: 1 bloqueio em 5 envios e ruido.
AMOSTRA_MINIMA_PARA_FREIO = 20


@dataclass(frozen=True)
class Perfil:
    """Configuracao escolhida pelo usuario no dashboard.

    O padrao e deliberadamente mais conservador que os limites da pesquisa: a
    meta e o numero sobreviver a terceira campanha, nao a primeira.

    Sobre o intervalo padrao de 120-300s: a pesquisa cita "~1 mensagem por
    minuto" e "abaixo de 30 mensagens/hora", que se contradizem - 1/min da 60/h.
    O teto horario e o limite que a fiscalizacao observa, entao ele vence: 30/h
    exige intervalo medio de 120s no minimo. Com 40/dia e media de 210s, a cota
    do dia leva ~2h20 para sair e se dilui na janela comercial, em vez de sair
    numa rajada de uma hora.
    """

    teto_diario: int = 40
    intervalo_min_seg: int = 120
    intervalo_max_seg: int = 300
    hora_inicio: time = time(9, 0)
    hora_fim: time = time(18, 0)
    dias_uteis_apenas: bool = True
    respeitar_aquecimento: bool = True

    def avisos(self) -> list[str]:
        """Riscos de uma configuracao mais agressiva que a pesquisa recomenda.

        Nao bloqueia: o usuario pode passar dos limites, mas ve o porque nao
        deveria.
        """
        alertas: list[str] = []

        if self.intervalo_min_seg < 30:
            alertas.append(
                "Intervalo abaixo de 30s passa de 60 mensagens/hora, que e o "
                "patamar onde a fiscalizacao do WhatsApp costuma agir."
            )
        elif self.intervalo_min_seg < 120:
            por_hora = 3600 // max(self.intervalo_min_seg, 1)
            if por_hora > TETO_HORA_SEGURO:
                alertas.append(
                    f"No intervalo minimo, o ritmo chega a {por_hora} mensagens/hora. "
                    f"O recomendado e ficar abaixo de {TETO_HORA_SEGURO}."
                )

        if self.intervalo_min_seg == self.intervalo_max_seg:
            alertas.append(
                "Intervalo fixo e assinatura de automacao. Deixe uma faixa para "
                "o envio variar."
            )

        if self.teto_diario > TETO_CONTA_MADURA:
            alertas.append(
                f"Acima de {TETO_CONTA_MADURA} mensagens/dia so e sustentavel na "
                "API oficial do WhatsApp, com template aprovado."
            )

        janela_horas = _horas_entre(self.hora_inicio, self.hora_fim)
        if janela_horas > 12:
            alertas.append(
                "Janela maior que 12 horas espalha o volume pelo dia inteiro, o "
                "que destoa de uso humano."
            )

        return alertas


@dataclass(frozen=True)
class Situacao:
    """Estado atual da conexao e da campanha, vindo do banco."""

    idade_conexao_dias: int
    enviadas_hoje: int
    enviadas_ultima_hora: int
    total_entregues: int
    total_respostas: int
    total_bloqueios: int


@dataclass(frozen=True)
class Decisao:
    """Resposta do motor: pode mandar agora? Se nao, por que?"""

    liberado: bool
    motivo: str = ""
    freio_permanente: bool = False


def teto_do_dia(perfil: Perfil, idade_conexao_dias: int) -> int:
    """Teto efetivo de hoje: o menor entre o do perfil e o do aquecimento."""
    if not perfil.respeitar_aquecimento:
        return perfil.teto_diario

    limite_aquecimento = TETO_CONTA_MADURA
    for dia_final, teto in DEGRAUS_AQUECIMENTO:
        if idade_conexao_dias <= dia_final:
            limite_aquecimento = teto
            break

    return min(perfil.teto_diario, limite_aquecimento)


def dentro_da_janela(perfil: Perfil, agora: datetime) -> bool:
    if perfil.dias_uteis_apenas and agora.weekday() >= 5:
        return False
    return perfil.hora_inicio <= agora.time() < perfil.hora_fim


def taxa_bloqueio(situacao: Situacao) -> float:
    if situacao.total_entregues <= 0:
        return 0.0
    return situacao.total_bloqueios / situacao.total_entregues


def taxa_resposta(situacao: Situacao) -> float:
    if situacao.total_entregues <= 0:
        return 0.0
    return situacao.total_respostas / situacao.total_entregues


def avaliar(perfil: Perfil, situacao: Situacao, agora: datetime) -> Decisao:
    """Decide se a proxima mensagem pode sair agora.

    A ordem importa: o freio de reputacao vem primeiro, porque e o unico que
    significa "pare e reveja o texto" em vez de "espere um pouco".
    """
    if situacao.total_entregues >= AMOSTRA_MINIMA_PARA_FREIO:
        bloqueio = taxa_bloqueio(situacao)
        if bloqueio > BLOQUEIO_MAXIMO:
            return Decisao(
                liberado=False,
                motivo=(
                    f"Taxa de bloqueio em {bloqueio:.1%}, acima do limite de "
                    f"{BLOQUEIO_MAXIMO:.0%}. Campanha pausada: revise o texto "
                    "antes de continuar."
                ),
                freio_permanente=True,
            )

        resposta = taxa_resposta(situacao)
        if resposta < RESPOSTA_MINIMA:
            return Decisao(
                liberado=False,
                motivo=(
                    f"Taxa de resposta em {resposta:.1%}, abaixo de "
                    f"{RESPOSTA_MINIMA:.0%}. Campanha pausada: a mensagem nao "
                    "esta engajando e isso derruba a reputacao do numero."
                ),
                freio_permanente=True,
            )

    teto = teto_do_dia(perfil, situacao.idade_conexao_dias)
    if situacao.enviadas_hoje >= teto:
        if perfil.respeitar_aquecimento and teto < perfil.teto_diario:
            return Decisao(
                liberado=False,
                motivo=(
                    f"Teto de aquecimento atingido ({teto} hoje). A conexao tem "
                    f"{situacao.idade_conexao_dias} dia(s); o limite sobe sozinho "
                    "conforme ela amadurece."
                ),
            )
        return Decisao(liberado=False, motivo=f"Teto diario atingido ({teto}).")

    if situacao.enviadas_ultima_hora >= TETO_HORA_SEGURO:
        return Decisao(
            liberado=False,
            motivo=(
                f"{situacao.enviadas_ultima_hora} mensagens na ultima hora. "
                f"Aguardando para ficar abaixo de {TETO_HORA_SEGURO}/hora."
            ),
        )

    if not dentro_da_janela(perfil, agora):
        return Decisao(
            liberado=False,
            motivo=(
                f"Fora da janela de envio "
                f"({perfil.hora_inicio:%H:%M} as {perfil.hora_fim:%H:%M}"
                f"{', dias uteis' if perfil.dias_uteis_apenas else ''})."
            ),
        )

    return Decisao(liberado=True)


def proximo_intervalo(perfil: Perfil, sorteio: random.Random | None = None) -> int:
    """Segundos ate o proximo envio, sorteados dentro da faixa do perfil."""
    rng = sorteio or random
    menor = min(perfil.intervalo_min_seg, perfil.intervalo_max_seg)
    maior = max(perfil.intervalo_min_seg, perfil.intervalo_max_seg)
    return rng.randint(menor, maior)


def restantes_hoje(perfil: Perfil, situacao: Situacao) -> int:
    teto = teto_do_dia(perfil, situacao.idade_conexao_dias)
    return max(0, teto - situacao.enviadas_hoje)


def com_teto(perfil: Perfil, teto_diario: int) -> Perfil:
    return replace(perfil, teto_diario=teto_diario)


def _horas_entre(inicio: time, fim: time) -> float:
    minutos = (fim.hour * 60 + fim.minute) - (inicio.hour * 60 + inicio.minute)
    return minutos / 60
