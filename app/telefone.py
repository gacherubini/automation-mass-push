"""Normalizacao de telefone brasileiro para o formato que o WhatsApp usa.

O Google Maps entrega telefone como "(51) 99898-4086". O WhatsApp identifica
contatos por JID, cujo numero e o E.164 sem o "+": "5551998984086".

Duas armadilhas moram aqui:

1. O nono digito. Celulares brasileiros ganharam um "9" na frente em 2012, mas
   o WhatsApp guarda numeros antigos das duas formas dependendo de quando a
   conta foi criada. Nao da para adivinhar - por isso `variantes()` devolve as
   duas possibilidades, e quem decide e a checagem online.

2. Telefone fixo. "(51) 3466-0454" tem 8 digitos e comeca com 2-5. Raramente
   tem WhatsApp, e mandar para numero inexistente conta como sinal de spam.
   Por isso classificamos antes de tentar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DDI_BRASIL = "55"

# DDDs validos no Brasil. Serve para descartar lixo cedo, antes de gastar uma
# checagem online.
DDDS_VALIDOS = frozenset(
    {
        11, 12, 13, 14, 15, 16, 17, 18, 19,
        21, 22, 24, 27, 28,
        31, 32, 33, 34, 35, 37, 38,
        41, 42, 43, 44, 45, 46, 47, 48, 49,
        51, 53, 54, 55,
        61, 62, 63, 64, 65, 66, 67, 68, 69,
        71, 73, 74, 75, 77, 79,
        81, 82, 83, 84, 85, 86, 87, 88, 89,
        91, 92, 93, 94, 95, 96, 97, 98, 99,
    }
)

CELULAR = "celular"
FIXO = "fixo"
INVALIDO = "invalido"


@dataclass(frozen=True)
class Telefone:
    """Resultado da normalizacao.

    `numero` e o E.164 sem "+", pronto para virar JID.
    `tipo` e um de CELULAR, FIXO ou INVALIDO.
    """

    numero: str
    ddd: int
    tipo: str
    original: str

    @property
    def valido(self) -> bool:
        return self.tipo != INVALIDO

    @property
    def provavel_whatsapp(self) -> bool:
        """Fixo quase nunca tem WhatsApp; nao vale gastar disparo."""
        return self.tipo == CELULAR


def _so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def normalizar(bruto: str) -> Telefone:
    """Converte um telefone escrito de qualquer jeito para E.164 sem "+"."""
    digitos = _so_digitos(bruto)

    # Alguns cadastros trazem o zero do DDD ("051 99898-4086").
    if len(digitos) in (11, 12) and digitos.startswith("0"):
        digitos = digitos[1:]

    # Ja veio com o DDI do Brasil.
    if len(digitos) in (12, 13) and digitos.startswith(DDI_BRASIL):
        digitos = digitos[len(DDI_BRASIL) :]

    if len(digitos) not in (10, 11):
        return Telefone("", 0, INVALIDO, bruto)

    ddd = int(digitos[:2])
    assinante = digitos[2:]

    if ddd not in DDDS_VALIDOS:
        return Telefone("", ddd, INVALIDO, bruto)

    primeiro = assinante[0]
    if len(assinante) == 9:
        # Celular novo: 9 digitos comecando com 9.
        tipo = CELULAR if primeiro == "9" else INVALIDO
    elif primeiro in "6789":
        # Celular antigo, ainda sem o nono digito.
        tipo = CELULAR
    elif primeiro in "2345":
        tipo = FIXO
    else:
        tipo = INVALIDO

    if tipo == INVALIDO:
        return Telefone("", ddd, INVALIDO, bruto)

    return Telefone(f"{DDI_BRASIL}{ddd}{assinante}", ddd, tipo, bruto)


def variantes(telefone: Telefone) -> list[str]:
    """Formas possiveis do mesmo celular, por causa do nono digito.

    Contas criadas antes de 2012 podem estar registradas sem o 9. A checagem
    online decide qual existe; aqui so oferecemos os candidatos, do mais
    provavel para o menos.
    """
    if not telefone.valido:
        return []
    if telefone.tipo == FIXO:
        return [telefone.numero]

    assinante = telefone.numero[len(DDI_BRASIL) + 2 :]
    prefixo = telefone.numero[: len(DDI_BRASIL) + 2]

    if len(assinante) == 9 and assinante.startswith("9"):
        return [telefone.numero, f"{prefixo}{assinante[1:]}"]
    if len(assinante) == 8:
        return [telefone.numero, f"{prefixo}9{assinante}"]
    return [telefone.numero]


def formatar_br(telefone: Telefone) -> str:
    """Volta para o formato legivel, para mostrar na tela."""
    if not telefone.valido:
        return telefone.original
    assinante = telefone.numero[len(DDI_BRASIL) + 2 :]
    meio = assinante[:-4]
    fim = assinante[-4:]
    return f"({telefone.ddd}) {meio}-{fim}"
