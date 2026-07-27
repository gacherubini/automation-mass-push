"""Montagem da primeira mensagem a partir dos modelos escritos pelo usuario.

Por que existe sorteio entre varios modelos, e nao um texto so:

  Texto identico repetido e o padrao mais facil de detectar do lado do
  WhatsApp - nao depende de ler o conteudo, basta comparar hashes. A pesquisa
  do projeto (ver README) aponta ~15 destinatarios por hora como teto para
  mensagem identica. Variar o texto nao e enfeite: e o que separa "uma pessoa
  falando com varias lojas" de "um robo colando a mesma frase".

Por isso o modulo pede uma LISTA de modelos e sorteia um por lead, e por isso
existe `diversidade()`: a tela precisa avisar quando ha variacoes de menos para
o tamanho da lista.

Como em `ritmo.proximo_intervalo`, o sorteio aceita um `random.Random` para o
teste ser deterministico. Nada aqui faz I/O.
"""

from __future__ import annotations

import math
import random
import string
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# As unicas lacunas que o usuario pode usar no modelo. Sao os campos que a
# planilha do Maps garante - telefone e link ficam de fora de proposito:
# mostrar para a loja que voce tem o numero dela e o dado dela soa invasivo.
LACUNAS_VALIDAS: tuple[str, ...] = ("nome", "categoria", "endereco", "busca")

# Quando a planilha nao traz o valor de uma lacuna, a mensagem nao pode sair
# com "None", com "{categoria}" cru nem com um buraco ("Vi a  no Maps"). Cada
# lacuna tem um substituto neutro que mantem a frase gramatical - o texto sai
# mais generico, mas sai inteiro e sem denunciar que veio de um molde.
SUBSTITUTOS: Mapping[str, str] = {
    "nome": "sua loja",
    "categoria": "o seu segmento",
    "endereco": "sua regiao",
    "busca": "sua regiao",
}

# Teto de destinatarios por texto identico apontado pela pesquisa (por hora).
# Usamos o mesmo numero como teto por variacao na lista inteira: e mais
# conservador que o limite horario e cabe numa recomendacao de uma linha.
MAX_DESTINATARIOS_POR_TEXTO = 15


class ModeloInvalido(ValueError):
    """O modelo escrito pelo usuario nao pode ser usado como esta."""


@dataclass(frozen=True)
class MensagemPronta:
    """Uma mensagem ja montada, para conferencia na tela antes do disparo."""

    nome: str
    telefone: str
    texto: str


@dataclass(frozen=True)
class Diversidade:
    """Quanto os modelos espalham o texto pela lista de destinatarios."""

    variacoes: int
    destinatarios: int
    por_variacao: int
    minimo_recomendado: int

    @property
    def suficiente(self) -> bool:
        return self.por_variacao <= MAX_DESTINATARIOS_POR_TEXTO

    def aviso(self) -> str:
        """Vazio quando esta bom; senao, o que fazer, em uma frase."""
        if self.suficiente:
            return ""
        return (
            f"{self.variacoes} variacao(oes) para {self.destinatarios} "
            f"destinatarios: cada texto sairia igual para {self.por_variacao} "
            f"numeros. Acima de {MAX_DESTINATARIOS_POR_TEXTO} com texto "
            f"identico e gatilho de spam - escreva pelo menos "
            f"{self.minimo_recomendado} variacoes."
        )


def lacunas_usadas(modelo: str) -> frozenset[str]:
    """Valida o modelo e devolve as lacunas que ele usa.

    Roda ANTES do disparo: um "{" solto ou um "{telefone}" que o usuario
    inventou precisa virar erro na tela, nunca uma mensagem torta saindo para
    trezentas lojas.
    """
    validas = ", ".join(f"{{{nome}}}" for nome in LACUNAS_VALIDAS)

    try:
        pedacos = list(string.Formatter().parse(modelo))
    except ValueError as erro:
        raise ModeloInvalido(
            "Chave desbalanceada no modelo: toda lacuna precisa abrir e fechar, "
            f'como {{nome}}. Para escrever uma chave de verdade use "{{{{" ou '
            f'"}}}}". Detalhe: {erro}'
        ) from erro

    usadas: set[str] = set()
    for _, campo, formato, conversao in pedacos:
        if campo is None:
            continue
        if formato or conversao:
            # "{nome:d}" so estouraria na hora do disparo, com a fila andando.
            raise ModeloInvalido(
                f"A lacuna {{{campo}}} tem formatacao extra, que nao e "
                f"suportada. Use apenas {validas}."
            )
        if campo == "":
            raise ModeloInvalido(
                f"Ha um {{}} sem nome no modelo. Lacunas validas: {validas}."
            )
        if campo not in LACUNAS_VALIDAS:
            raise ModeloInvalido(
                f"Lacuna desconhecida no modelo: {{{campo}}}. "
                f"Lacunas validas: {validas}."
            )
        usadas.add(campo)

    return frozenset(usadas)


def validar(modelos: Sequence[str]) -> None:
    """Valida a lista inteira, apontando a posicao do modelo com problema."""
    if not modelos:
        raise ModeloInvalido(
            "Escreva pelo menos um modelo de mensagem antes de disparar."
        )

    for posicao, modelo in enumerate(modelos, start=1):
        if not modelo or not modelo.strip():
            raise ModeloInvalido(f"O modelo {posicao} esta vazio.")
        try:
            lacunas_usadas(modelo)
        except ModeloInvalido as erro:
            raise ModeloInvalido(f"Modelo {posicao}: {erro}") from erro


def montar(modelo: str, lead: Any) -> str:
    """Preenche um modelo com os dados de um lead."""
    valores = {
        lacuna: _valor(lead, lacuna) or SUBSTITUTOS[lacuna]
        for lacuna in lacunas_usadas(modelo)
    }
    # `format_map` com as lacunas ja validadas: qualquer chave restante seria
    # erro de validacao, que ja teria estourado acima.
    return modelo.format_map(valores)


def sortear(modelos: Sequence[str], sorteio: random.Random | None = None) -> str:
    """Escolhe uma das variacoes ao acaso.

    Ao acaso, e nao em rodizio: rodizio deixa o texto amarrado a ordem da fila,
    e a ordem da fila e outro padrao repetido.
    """
    validar(modelos)
    rng = sorteio or random
    return rng.choice(list(modelos))


def montar_para(
    modelos: Sequence[str], lead: Any, sorteio: random.Random | None = None
) -> str:
    """Sorteia uma variacao e monta a mensagem daquele lead."""
    return montar(sortear(modelos, sorteio), lead)


def previa(
    modelos: Sequence[str],
    leads: Iterable[Any],
    sorteio: random.Random | None = None,
) -> list[MensagemPronta]:
    """Mensagens ja montadas, para o usuario conferir antes de disparar.

    Mesma funcao que o disparo usaria, entao o que aparece na tela e literalmente
    o que vai sair.
    """
    validar(modelos)
    rng = sorteio or random
    return [
        MensagemPronta(
            nome=_valor(lead, "nome"),
            telefone=_valor(lead, "telefone"),
            texto=montar(rng.choice(list(modelos)), lead),
        )
        for lead in leads
    ]


def diversidade(modelos: Sequence[str], destinatarios: int) -> Diversidade:
    """Quantos destinatarios distintos receberiam o mesmo texto.

    Textos repetidos na lista nao contam como variacao - colar o mesmo
    paragrafo duas vezes nao engana ninguem do outro lado.
    """
    validar(modelos)

    distintos = {" ".join(modelo.split()) for modelo in modelos}
    variacoes = len(distintos)
    destinatarios = max(0, destinatarios)

    return Diversidade(
        variacoes=variacoes,
        destinatarios=destinatarios,
        por_variacao=math.ceil(destinatarios / variacoes),
        minimo_recomendado=max(
            1, math.ceil(destinatarios / MAX_DESTINATARIOS_POR_TEXTO)
        ),
    )


def _valor(lead: Any, campo: str) -> str:
    """Le um campo do lead aceitando dataclass ou dicionario.

    O modulo nao importa `planilha` de proposito: qualquer objeto com os campos
    certos serve, inclusive um dict vindo do banco.
    """
    if isinstance(lead, Mapping):
        bruto = lead.get(campo, "")
    else:
        bruto = getattr(lead, campo, "")
    return "" if bruto is None else str(bruto).strip()
