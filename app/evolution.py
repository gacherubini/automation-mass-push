"""Cliente da Evolution API v2 - a ponte entre o sistema e o WhatsApp do usuario.

A Evolution roda self-hosted e expoe o WhatsApp Web (Baileys) como HTTP. Cada
usuario do dashboard vira uma "instancia": ele le um QR code e a partir dai as
mensagens saem do numero dele.

Endpoints usados (confirmados no codigo do projeto, nao na documentacao, que
esta incompleta - ver as notas no README do modulo abaixo):

    POST   /instance/create
    GET    /instance/connect/{instancia}
    GET    /instance/connectionState/{instancia}
    GET    /instance/fetchInstances?instanceName={instancia}
    DELETE /instance/logout/{instancia}
    DELETE /instance/delete/{instancia}
    POST   /chat/whatsappNumbers/{instancia}
    POST   /message/sendText/{instancia}

Tres decisoes deste modulo merecem destaque:

1. **Envio de mensagem nunca e repetido.** Um timeout num POST /message/sendText
   nao significa que a mensagem nao saiu - significa que nao sabemos. Repetir
   pode entregar a mesma mensagem duas vezes para um lead que nunca nos
   escreveu, e mensagem duplicada e exatamente o padrao que a fiscalizacao do
   WhatsApp classifica como spam. Uma falha visivel na tela e sempre melhor.

2. **Checagem de numero em lote, mas em lotes pequenos.** O endpoint aceita uma
   lista de uma vez, e isso e essencial porque boa parte dos telefones do Google
   Maps e fixo. Mas a issue #2228 do proprio projeto relata banimento por checar
   volume alto de numeros de uma vez - por isso `LOTE_MAXIMO_CHECAGEM`.

3. **Nada de dicionario cru saindo daqui.** Todo retorno e dataclass frozen. O
   resto do sistema nunca precisa saber que `key.remoteJid` existe.

Nenhuma funcao aqui abre conexao por conta propria alem do `httpx.Client`: o
cliente pode ser injetado no construtor, que e o que torna o teste possivel sem
rede.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

INTEGRACAO_BAILEYS = "WHATSAPP-BAILEYS"

# Estados de conexao, ja traduzidos. A Evolution usa close/connecting/open.
DESCONECTADA = "desconectada"
AGUARDANDO_QR = "aguardando_qr"
CONECTADA = "conectada"
DESCONHECIDO = "desconhecido"

_ESTADOS_DA_API = {
    "close": DESCONECTADA,
    "connecting": AGUARDANDO_QR,
    "open": CONECTADA,
}

EVENTO_MENSAGEM = "messages.upsert"

SUFIXO_USUARIO = "@s.whatsapp.net"
SUFIXO_GRUPO = "@g.us"
SUFIXO_LID = "@lid"

# Eventos que o webhook precisa receber. Assinar so o necessario reduz ruido e
# carga: um numero em campanha recebe muito evento de presenca e de contato que
# nao interessa a ninguem aqui.
EVENTOS_PADRAO: tuple[str, ...] = ("MESSAGES_UPSERT", "CONNECTION_UPDATE")

# Teto por requisicao de checagem. Ver issue #2228: checar muitos numeros de uma
# vez e um dos gatilhos de banimento relatados.
LOTE_MAXIMO_CHECAGEM = 50

# Timeouts. Nenhuma chamada fica sem teto - um socket pendurado trava a fila de
# disparo inteira.
TIMEOUT_CONEXAO_SEG = 5.0
TIMEOUT_PADRAO_SEG = 15.0
# /instance/create e /instance/connect dormem de proposito no servidor (5s e 2s)
# esperando o QR aparecer, entao precisam de folga bem maior que o resto.
TIMEOUT_QRCODE_SEG = 60.0
# O envio aceita um "delay" de digitacao que o servidor cumpre antes de mandar;
# esse tempo entra no timeout em `enviar_texto`.
TIMEOUT_ENVIO_SEG = 60.0

TENTATIVAS_PADRAO = 3
ESPERA_BASE_SEG = 1.0
ESPERA_MAXIMA_SEG = 8.0


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------


class ErroEvolution(Exception):
    """Base de todos os erros deste modulo.

    A mensagem e escrita para aparecer direto na tela do usuario, entao fala de
    WhatsApp e de conexao, nao de HTTP.
    """

    def __init__(self, mensagem: str, *, status: int | None = None, detalhe: Any = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status
        self.detalhe = detalhe


class FalhaDeAutenticacao(ErroEvolution):
    """Chave de API recusada pelo servidor."""


class InstanciaNaoEncontrada(ErroEvolution):
    """A instancia nao existe mais no servidor (foi apagada ou nunca criada)."""


class RequisicaoRecusada(ErroEvolution):
    """O servidor entendeu e recusou. Repetir igual nao vai adiantar."""


class InstanciaJaExiste(RequisicaoRecusada):
    """Ja existe instancia com esse nome."""


class NumeroInvalido(RequisicaoRecusada):
    """Numero mal formado ou sem conta de WhatsApp."""


class JaConectado(ErroEvolution):
    """Pediram QR code de uma instancia que ja esta conectada."""


class ServicoIndisponivel(ErroEvolution):
    """Timeout, queda de rede ou 5xx - o unico grupo que vale repetir."""


class RespostaInesperada(ErroEvolution):
    """O servidor respondeu algo que nao da para interpretar."""


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QrCode:
    """QR code que o dashboard mostra para o usuario apontar o celular."""

    base64: str = ""
    codigo: str = ""
    contagem: int = 0
    pairing_code: str = ""

    @property
    def vazio(self) -> bool:
        return not (self.base64 or self.codigo or self.pairing_code)

    @property
    def imagem(self) -> str:
        """Data URI pronto para o `src` de um `<img>`.

        A Evolution as vezes ja devolve com o prefixo `data:image/png;base64,` e
        as vezes so o base64 puro; normalizamos para o template nao precisar
        saber disso.
        """
        if not self.base64:
            return ""
        if self.base64.startswith("data:"):
            return self.base64
        return f"data:image/png;base64,{self.base64}"


@dataclass(frozen=True)
class Instancia:
    """Resultado de criar a instancia de um usuario."""

    nome: str
    instancia_id: str = ""
    token: str = ""
    estado: str = DESCONHECIDO
    qrcode: QrCode | None = None


@dataclass(frozen=True)
class EstadoConexao:
    """Como esta a conexao do usuario agora."""

    nome: str
    estado: str
    numero: str = ""
    perfil: str = ""

    @property
    def conectada(self) -> bool:
        return self.estado == CONECTADA

    @property
    def pode_disparar(self) -> bool:
        """So conta como pronta para disparo quando ha numero confirmado."""
        return self.conectada and bool(self.numero)


@dataclass(frozen=True)
class ChecagemNumero:
    """Resposta da pergunta "esse telefone tem WhatsApp?".

    `numero` e o numero **canonico** devolvido pelo servidor, extraido do JID.
    Ele pode ser diferente do consultado: e o WhatsApp resolvendo o nono digito
    (ver `app/telefone.py`). Sempre dispare para `numero`, nunca para
    `consultado`.
    """

    consultado: str
    existe: bool
    jid: str = ""
    numero: str = ""
    nome: str = ""


@dataclass(frozen=True)
class MensagemEnviada:
    """Confirmacao de um envio aceito pelo servidor."""

    id_mensagem: str
    jid: str
    numero: str
    status: str = ""
    timestamp: int = 0


@dataclass(frozen=True)
class RespostaRecebida:
    """Mensagem que chegou pelo webhook.

    `do_proprio_dono` e o `key.fromMe` da Evolution: a API tambem avisa sobre as
    mensagens que **nos** mandamos. Sem esse filtro o sistema contaria o proprio
    disparo como resposta do lead e o freio de reputacao do `app/ritmo.py`
    ficaria cego.
    """

    instancia: str
    numero: str
    jid: str
    texto: str
    do_proprio_dono: bool
    nome_exibicao: str = ""
    id_mensagem: str = ""
    tipo: str = ""
    timestamp: int = 0
    e_grupo: bool = False

    @property
    def e_resposta_de_lead(self) -> bool:
        """Vale como resposta de lead para efeito de metrica e de opt-out?

        Eco do proprio disparo, grupo e mensagem sem texto (figurinha, audio,
        status) nao contam.
        """
        if self.do_proprio_dono or self.e_grupo:
            return False
        return bool(self.texto.strip())


# ---------------------------------------------------------------------------
# Auxiliares de leitura de payload
# ---------------------------------------------------------------------------


def _so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def _numero_do_jid(jid: str) -> str:
    """Extrai o numero de um JID.

    "5551998984086:12@s.whatsapp.net" -> "5551998984086". O sufixo depois dos
    dois-pontos e o id do dispositivo e nao faz parte do numero.
    """
    if not jid:
        return ""
    usuario = jid.split("@", 1)[0]
    usuario = usuario.split(":", 1)[0]
    return _so_digitos(usuario)


def _dict(valor: Any) -> dict[str, Any]:
    """Devolve um dict mesmo quando o campo veio nulo ou com outro tipo."""
    return dict(valor) if isinstance(valor, Mapping) else {}


def _texto(valor: Any) -> str:
    return valor if isinstance(valor, str) else ""


def _inteiro(valor: Any) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def _extrair_qrcode(dado: Mapping[str, Any]) -> QrCode:
    """Le o QR code de um payload.

    A Evolution devolve o QR em dois formatos conforme o endpoint: aninhado em
    `qrcode` (resposta do /instance/create) ou solto na raiz (resposta do
    /instance/connect). Aceitamos os dois.
    """
    bruto = _dict(dado.get("qrcode")) or dado
    return QrCode(
        base64=_texto(bruto.get("base64")),
        codigo=_texto(bruto.get("code")),
        contagem=_inteiro(bruto.get("count")),
        pairing_code=_texto(bruto.get("pairingCode")),
    )


# Ordem de busca do texto de uma mensagem. A Evolution normaliza
# `extendedTextMessage` para `conversation` antes de mandar o webhook, mas nem
# toda versao faz isso - por isso as duas entradas.
_CAMPOS_DE_TEXTO: tuple[tuple[str, str | None], ...] = (
    ("conversation", None),
    ("extendedTextMessage", "text"),
    ("imageMessage", "caption"),
    ("videoMessage", "caption"),
    ("documentMessage", "caption"),
    ("buttonsResponseMessage", "selectedDisplayText"),
    ("templateButtonReplyMessage", "selectedDisplayText"),
    ("listResponseMessage", "title"),
)

# Envelopes que so embrulham a mensagem de verdade.
_ENVELOPES = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage")


def _texto_da_mensagem(mensagem: Mapping[str, Any], profundidade: int = 0) -> str:
    """Acha o texto dentro do objeto `message` do Baileys.

    O mesmo texto mora em lugares diferentes conforme o tipo da mensagem, e
    mensagens efemeras vem embrulhadas em mais uma camada. Sem isso, uma
    resposta de lead escrita como "mensagem temporaria" passaria batida.
    """
    if not isinstance(mensagem, Mapping) or profundidade > 4:
        return ""

    for chave, subchave in _CAMPOS_DE_TEXTO:
        valor = mensagem.get(chave)
        if subchave is None:
            if _texto(valor):
                return _texto(valor)
            continue
        achado = _texto(_dict(valor).get(subchave))
        if achado:
            return achado

    for envelope in _ENVELOPES:
        interno = _dict(mensagem.get(envelope)).get("message")
        if interno:
            achado = _texto_da_mensagem(_dict(interno), profundidade + 1)
            if achado:
                return achado

    return ""


def _normalizar_evento(nome: str) -> str:
    """"MESSAGES_UPSERT", "messages-upsert" e "messages.upsert" sao o mesmo evento.

    Qual chega depende de o webhook estar configurado com `byEvents`; o sistema
    nao pode depender dessa configuracao.
    """
    return re.sub(r"[-_]", ".", _texto(nome).strip().lower())


def interpretar_webhook(payload: Mapping[str, Any]) -> RespostaRecebida | None:
    """Traduz o webhook da Evolution em algo que o resto do sistema entende.

    Devolve `None` quando o payload nao e uma mensagem recebida (outro evento,
    ou corpo sem `key`). Mensagem com `fromMe=true` **nao** vira `None`: ela
    volta com `do_proprio_dono=True` para quem chamou poder registrar o eco do
    proprio disparo se quiser. Quem so quer resposta de lead usa
    `resposta.e_resposta_de_lead`.
    """
    if not isinstance(payload, Mapping):
        return None

    if _normalizar_evento(_texto(payload.get("event"))) != EVENTO_MENSAGEM:
        return None

    dado = _dict(payload.get("data"))
    # messages.upsert as vezes chega como lista de mensagens em vez de uma so.
    if not dado:
        lista = payload.get("data")
        if isinstance(lista, Sequence) and not isinstance(lista, (str, bytes)) and lista:
            dado = _dict(lista[0])
    if not dado:
        return None

    chave = _dict(dado.get("key"))
    if not chave:
        return None

    jid = _texto(chave.get("remoteJid"))
    # Numeros em modo LID vem com um identificador opaco no remoteJid; o numero
    # de verdade fica no remoteJidAlt. Sem isso o lead entraria na base com um
    # "numero" que nao da para ligar de volta.
    if SUFIXO_LID in jid and _texto(chave.get("remoteJidAlt")):
        jid = _texto(chave.get("remoteJidAlt"))

    e_grupo = jid.endswith(SUFIXO_GRUPO)
    # Em grupo o remoteJid e o grupo; quem escreveu esta em `participant`.
    jid_remetente = _texto(chave.get("participant")) if e_grupo else jid

    return RespostaRecebida(
        instancia=_texto(payload.get("instance")),
        numero=_numero_do_jid(jid_remetente),
        jid=jid,
        texto=_texto_da_mensagem(_dict(dado.get("message"))),
        do_proprio_dono=bool(chave.get("fromMe")),
        nome_exibicao=_texto(dado.get("pushName")),
        id_mensagem=_texto(chave.get("id")),
        tipo=_texto(dado.get("messageType")),
        timestamp=_inteiro(dado.get("messageTimestamp")),
        e_grupo=e_grupo,
    )


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Politica:
    """Parametros de repeticao, separados para ficarem faceis de ver e testar."""

    tentativas: int = TENTATIVAS_PADRAO
    espera_base: float = ESPERA_BASE_SEG
    espera_maxima: float = ESPERA_MAXIMA_SEG

    def espera(self, tentativa: int) -> float:
        """Backoff exponencial, com teto para nao travar a fila."""
        return min(self.espera_base * (2 ** (tentativa - 1)), self.espera_maxima)


class Evolution:
    """Cliente sincrono da Evolution API v2.

    O `httpx.Client` e opcional: sem ele o cliente cria o proprio e fecha no
    `fechar()`. Passar um pronto e o que permite testar tudo com
    `httpx.MockTransport`, sem rede nenhuma.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        cliente: httpx.Client | None = None,
        *,
        tentativas: int = TENTATIVAS_PADRAO,
        espera_base: float = ESPERA_BASE_SEG,
        timeout_padrao: float = TIMEOUT_PADRAO_SEG,
        timeout_qrcode: float = TIMEOUT_QRCODE_SEG,
        timeout_envio: float = TIMEOUT_ENVIO_SEG,
        timeout_conexao: float = TIMEOUT_CONEXAO_SEG,
        dormir: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.politica = _Politica(tentativas=max(1, tentativas), espera_base=espera_base)
        self._dormir = dormir
        self._timeout_conexao = timeout_conexao
        self._timeout_padrao = timeout_padrao
        self._timeout_qrcode = timeout_qrcode
        self._timeout_envio = timeout_envio
        self._cliente = cliente or httpx.Client()
        self._cliente_proprio = cliente is None

    # -- ciclo de vida ------------------------------------------------------

    def fechar(self) -> None:
        """Fecha apenas o cliente que nos mesmos criamos.

        Fechar um cliente injetado seria roubar um recurso de quem o passou.
        """
        if self._cliente_proprio:
            self._cliente.close()

    def __enter__(self) -> Evolution:
        return self

    def __exit__(self, *_) -> None:
        self.fechar()

    # -- operacoes ----------------------------------------------------------

    def criar_instancia(
        self,
        nome: str,
        *,
        numero: str = "",
        webhook_url: str = "",
        eventos: Sequence[str] = EVENTOS_PADRAO,
        token: str = "",
        ignorar_grupos: bool = True,
        com_qrcode: bool = True,
    ) -> Instancia:
        """Cria a instancia de um usuario e ja pede o QR code.

        `ignorar_grupos` vem ligado de proposito: prospeccao fala com loja, nao
        com grupo, e cada evento de grupo que chega e ruido no webhook.
        """
        corpo: dict[str, Any] = {
            "instanceName": nome,
            "integration": INTEGRACAO_BAILEYS,
            "qrcode": com_qrcode,
            "groupsIgnore": ignorar_grupos,
            # Nada de marcar presenca falsa nem ler mensagem sozinho: quanto
            # menos comportamento automatico visivel, melhor.
            "alwaysOnline": False,
            "readMessages": False,
            "readStatus": False,
            "syncFullHistory": False,
        }
        if numero:
            corpo["number"] = _so_digitos(numero)
        if token:
            corpo["token"] = token
        if webhook_url:
            corpo["webhook"] = {
                "enabled": True,
                "url": webhook_url,
                "byEvents": False,
                "base64": False,
                "events": list(eventos),
            }

        dado = self._requisitar(
            "POST", "/instance/create", corpo=corpo, timeout=self._timeout_qrcode
        )
        return self._ler_instancia(nome, dado)

    def obter_qrcode(self, nome: str) -> QrCode:
        """Busca o QR code para o dashboard exibir.

        Levanta `JaConectado` se a instancia ja esta conectada - nesse caso nao
        ha QR nenhum e mostrar um quadrado vazio confundiria o usuario.
        """
        dado = self._requisitar(
            "GET", f"/instance/connect/{nome}", timeout=self._timeout_qrcode
        )

        # /instance/connect engole a excecao e devolve 200 com {error: true}.
        self._verificar_erro_embutido(nome, dado)

        estado = _ESTADOS_DA_API.get(_texto(_dict(dado.get("instance")).get("state")))
        if estado == CONECTADA:
            raise JaConectado(
                f'A conexao "{nome}" ja esta ativa. Desconecte antes de ler um novo QR code.'
            )

        qrcode = _extrair_qrcode(dado)
        if qrcode.vazio:
            raise RespostaInesperada(
                f'O servidor nao devolveu QR code para "{nome}". Tente novamente em alguns segundos.',
                detalhe=dado,
            )
        return qrcode

    def estado_conexao(self, nome: str) -> EstadoConexao:
        """Estado atual da conexao e, quando conectada, o numero do usuario."""
        dado = self._requisitar(
            "GET", f"/instance/connectionState/{nome}", timeout=self._timeout_padrao
        )
        instancia = _dict(dado.get("instance"))
        # `state` some do JSON quando a instancia existe no banco mas ainda nao
        # foi carregada na memoria do servidor. Isso e "nao sei", nao "caida".
        estado = _ESTADOS_DA_API.get(_texto(instancia.get("state")), DESCONHECIDO)

        if estado != CONECTADA:
            return EstadoConexao(nome=nome, estado=estado)

        numero, perfil = self._dono_da_instancia(nome)
        return EstadoConexao(nome=nome, estado=estado, numero=numero, perfil=perfil)

    def desconectar(self, nome: str) -> bool:
        """Derruba a sessao do WhatsApp (o celular perde o dispositivo pareado).

        Devolve `False` quando a instancia ja estava desconectada, porque para
        quem chamou o resultado e o mesmo e um erro ai so geraria alarme falso.
        """
        try:
            self._requisitar(
                "DELETE", f"/instance/logout/{nome}", timeout=self._timeout_padrao
            )
        except RequisicaoRecusada as erro:
            if "not connected" in str(erro.detalhe or "").lower():
                return False
            raise
        return True

    def remover_instancia(self, nome: str) -> bool:
        """Apaga a instancia do servidor. Desconecta antes, se preciso."""
        self._requisitar("DELETE", f"/instance/delete/{nome}", timeout=self._timeout_padrao)
        return True

    def checar_numeros(self, nome: str, numeros: Sequence[str]) -> list[ChecagemNumero]:
        """Diz quais desses telefones tem WhatsApp.

        Esta e a checagem mais importante do sistema: metade dos telefones que
        vem do Google Maps e fixo, e disparar para numero inexistente e sinal de
        spam que ajuda a derrubar o numero do usuario.

        O resultado sai na mesma ordem da entrada. Numero que o servidor deixou
        de responder volta como `existe=False`: na duvida, nao dispara.
        """
        pedidos = [n for n in (_so_digitos(x) for x in numeros) if n]
        if not pedidos:
            # Sem numero nao ha o que perguntar - poupa uma chamada.
            return []

        por_numero: dict[str, ChecagemNumero] = {}
        for lote in _em_lotes(pedidos, LOTE_MAXIMO_CHECAGEM):
            dado = self._requisitar(
                "POST",
                f"/chat/whatsappNumbers/{nome}",
                corpo={"numbers": list(lote)},
                timeout=self._timeout_padrao,
            )
            for item in _lista(dado):
                registro = _dict(item)
                jid = _texto(registro.get("jid"))
                consultado = _so_digitos(_texto(registro.get("number")))
                por_numero[consultado] = ChecagemNumero(
                    consultado=consultado,
                    existe=bool(registro.get("exists")),
                    jid=jid,
                    numero=_numero_do_jid(jid),
                    nome=_texto(registro.get("name")),
                )

        return [
            por_numero.get(n, ChecagemNumero(consultado=n, existe=False)) for n in pedidos
        ]

    def checar_numero(self, nome: str, numero: str) -> ChecagemNumero:
        """Versao de um numero so. Prefira `checar_numeros` para listas."""
        resultado = self.checar_numeros(nome, [numero])
        if not resultado:
            raise NumeroInvalido(f'"{numero}" nao e um telefone que de para consultar.')
        return resultado[0]

    def enviar_texto(
        self,
        nome: str,
        numero: str,
        texto: str,
        *,
        delay_ms: int = 0,
        link_preview: bool | None = None,
    ) -> MensagemEnviada:
        """Manda uma mensagem de texto. **Sem repeticao, em hipotese alguma.**

        Se der timeout ou 5xx aqui, o cliente falha na hora em vez de tentar de
        novo. O motivo: o POST pode ter chegado e a mensagem pode ter saido - o
        que se perdeu foi a resposta. Repetir entregaria a mesma primeira
        mensagem duas vezes para um lead que nunca nos escreveu, que e
        literalmente a definicao de spam para a fiscalizacao do WhatsApp. Uma
        falha visivel na tela e recuperavel; um lead irritado e um numero
        marcado nao sao.
        """
        corpo: dict[str, Any] = {"number": _so_digitos(numero), "text": texto}
        if delay_ms > 0:
            corpo["delay"] = delay_ms
        if link_preview is not None:
            corpo["linkPreview"] = link_preview

        # O servidor cumpre o delay de digitacao antes de mandar; se o timeout
        # nao contasse esse tempo, todo envio com delay morreria por timeout.
        timeout = self._timeout_envio + (delay_ms / 1000)

        dado = self._requisitar(
            "POST",
            f"/message/sendText/{nome}",
            corpo=corpo,
            timeout=timeout,
            permitir_retry=False,
        )

        chave = _dict(dado.get("key"))
        jid = _texto(chave.get("remoteJid"))
        return MensagemEnviada(
            id_mensagem=_texto(chave.get("id")),
            jid=jid,
            numero=_numero_do_jid(jid) or _so_digitos(numero),
            status=_texto(dado.get("status")),
            timestamp=_inteiro(dado.get("messageTimestamp")),
        )

    # -- internos -----------------------------------------------------------

    def _dono_da_instancia(self, nome: str) -> tuple[str, str]:
        """Numero e nome de perfil de quem escaneou o QR.

        /instance/connectionState nao devolve o numero; quem tem isso e o
        /instance/fetchInstances, que le do banco. Falha aqui nao derruba a
        leitura do estado: saber que esta conectado ja e util, e o numero e
        enfeite de tela.
        """
        try:
            dado = self._requisitar(
                "GET",
                "/instance/fetchInstances",
                params={"instanceName": nome},
                timeout=self._timeout_padrao,
            )
        except ErroEvolution:
            return "", ""

        for item in _lista(dado):
            registro = _dict(item)
            if _texto(registro.get("name")) not in (nome, ""):
                continue
            return (
                _numero_do_jid(_texto(registro.get("ownerJid"))),
                _texto(registro.get("profileName")),
            )
        return "", ""

    def _ler_instancia(self, nome: str, dado: Mapping[str, Any]) -> Instancia:
        instancia = _dict(dado.get("instance"))
        # `hash` e uma string na v2.2+, mas versoes v2.0 devolvem
        # {"apikey": "..."}. Aceitamos as duas para nao quebrar em upgrade.
        bruto_hash = dado.get("hash")
        token = (
            bruto_hash if isinstance(bruto_hash, str) else _texto(_dict(bruto_hash).get("apikey"))
        )

        qrcode = _extrair_qrcode(dado)
        return Instancia(
            nome=_texto(instancia.get("instanceName")) or nome,
            instancia_id=_texto(instancia.get("instanceId")),
            token=token,
            estado=_ESTADOS_DA_API.get(_texto(instancia.get("status")), DESCONHECIDO),
            qrcode=None if qrcode.vazio else qrcode,
        )

    def _verificar_erro_embutido(self, nome: str, dado: Mapping[str, Any]) -> None:
        """Trata o 200-com-erro do /instance/connect.

        O controller da Evolution captura a excecao e responde
        `{"error": true, "message": "..."}` com status 200. Sem isso, uma
        instancia apagada apareceria como "QR code vazio".
        """
        if not dado.get("error"):
            return
        mensagem = _texto(dado.get("message"))
        if "does not exist" in mensagem.lower():
            raise InstanciaNaoEncontrada(
                f'A conexao "{nome}" nao existe mais no servidor. Crie novamente.',
                detalhe=mensagem,
            )
        raise ErroEvolution(
            f'O servidor recusou conectar "{nome}": {mensagem}', detalhe=mensagem
        )

    def _url(self, caminho: str) -> str:
        return f"{self.base_url}{caminho}"

    def _requisitar(
        self,
        metodo: str,
        caminho: str,
        *,
        corpo: Any = None,
        params: Mapping[str, Any] | None = None,
        timeout: float = TIMEOUT_PADRAO_SEG,
        permitir_retry: bool = True,
    ) -> Any:
        """Faz a chamada, repetindo apenas o que vale a pena repetir.

        Repete: timeout, erro de conexao e 5xx - falhas onde o servidor
        provavelmente nem processou o pedido.
        Nao repete: 4xx (repetir da o mesmo erro) e nada com
        `permitir_retry=False`, que e como o envio de mensagem se protege.
        """
        tentativas = self.politica.tentativas if permitir_retry else 1
        limite = httpx.Timeout(timeout, connect=self._timeout_conexao)
        cabecalhos = {"apikey": self.api_key, "Content-Type": "application/json"}
        ultimo: ErroEvolution | None = None

        for tentativa in range(1, tentativas + 1):
            try:
                resposta = self._cliente.request(
                    metodo,
                    self._url(caminho),
                    json=corpo,
                    params=dict(params) if params else None,
                    headers=cabecalhos,
                    timeout=limite,
                )
            except httpx.TimeoutException as erro:
                ultimo = ServicoIndisponivel(
                    "O servidor do WhatsApp demorou demais para responder. "
                    "Verifique se a Evolution API esta no ar.",
                    detalhe=str(erro),
                )
            except httpx.TransportError as erro:
                ultimo = ServicoIndisponivel(
                    "Nao foi possivel falar com o servidor do WhatsApp. "
                    "Verifique a conexao e o endereco configurado.",
                    detalhe=str(erro),
                )
            else:
                if resposta.status_code >= 500:
                    ultimo = ServicoIndisponivel(
                        f"O servidor do WhatsApp respondeu com erro interno "
                        f"({resposta.status_code}). Tente novamente em instantes.",
                        status=resposta.status_code,
                        detalhe=_corpo_de_texto(resposta),
                    )
                else:
                    return self._interpretar(resposta)

            if tentativa < tentativas:
                self._dormir(self.politica.espera(tentativa))

        assert ultimo is not None  # so chega aqui apos alguma falha
        raise ultimo

    def _interpretar(self, resposta: httpx.Response) -> Any:
        if resposta.status_code >= 400:
            raise self._erro_de(resposta)
        if resposta.status_code == 204 or not resposta.content:
            return {}
        try:
            return resposta.json()
        except ValueError as erro:
            raise RespostaInesperada(
                "O servidor do WhatsApp devolveu uma resposta que nao da para ler.",
                status=resposta.status_code,
                detalhe=_corpo_de_texto(resposta),
            ) from erro

    def _erro_de(self, resposta: httpx.Response) -> ErroEvolution:
        """Traduz o corpo de erro da Evolution em excecao deste modulo.

        Formato do servidor (ver o middleware de erro do projeto):
        `{"status": 401, "error": "Unauthorized", "response": {"message": ...}}`,
        onde `message` pode ser texto, lista de textos ou objeto.
        """
        status = resposta.status_code
        corpo = _corpo_json(resposta)
        detalhe = _dict(corpo.get("response")).get("message", corpo.get("message"))
        texto = _mensagem_legivel(detalhe) or _texto(corpo.get("error")) or resposta.text

        if status == 401:
            return FalhaDeAutenticacao(
                "Chave de API recusada pelo servidor do WhatsApp. "
                "Confira a configuracao da Evolution API.",
                status=status,
                detalhe=detalhe,
            )

        if status == 404:
            return InstanciaNaoEncontrada(
                f"Conexao nao encontrada no servidor do WhatsApp. {texto}".strip(),
                status=status,
                detalhe=detalhe,
            )

        if status == 403:
            # O guard de criacao usa 403 tanto para "chave sem permissao" quanto
            # para "esse nome ja existe". So da para separar pelo texto.
            if "already in use" in texto.lower():
                return InstanciaJaExiste(
                    f"Ja existe uma conexao com esse nome no servidor. {texto}".strip(),
                    status=status,
                    detalhe=detalhe,
                )
            return FalhaDeAutenticacao(
                f"A chave de API nao tem permissao para essa operacao. {texto}".strip(),
                status=status,
                detalhe=detalhe,
            )

        if status in (400, 422):
            if _e_problema_de_numero(detalhe, texto):
                return NumeroInvalido(
                    "Esse numero nao tem WhatsApp ou esta em formato invalido. "
                    "O envio foi cancelado.",
                    status=status,
                    detalhe=detalhe,
                )
            return RequisicaoRecusada(
                f"O servidor do WhatsApp recusou o pedido. {texto}".strip(),
                status=status,
                detalhe=detalhe,
            )

        return ErroEvolution(
            f"O servidor do WhatsApp respondeu {status}. {texto}".strip(),
            status=status,
            detalhe=detalhe,
        )


# ---------------------------------------------------------------------------
# Auxiliares soltos
# ---------------------------------------------------------------------------


def _em_lotes(itens: Sequence[str], tamanho: int) -> Iterable[Sequence[str]]:
    for inicio in range(0, len(itens), tamanho):
        yield itens[inicio : inicio + tamanho]


def _lista(dado: Any) -> list[Any]:
    """Aceita lista crua ou lista embrulhada, que e como as versoes divergem."""
    if isinstance(dado, list):
        return dado
    if isinstance(dado, Mapping):
        for chave in ("data", "response", "result"):
            interno = dado.get(chave)
            if isinstance(interno, list):
                return interno
    return []


def _corpo_json(resposta: httpx.Response) -> dict[str, Any]:
    try:
        return _dict(resposta.json())
    except ValueError:
        return {}


def _corpo_de_texto(resposta: httpx.Response) -> str:
    try:
        return resposta.text[:500]
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return ""


def _mensagem_legivel(detalhe: Any) -> str:
    if isinstance(detalhe, str):
        return detalhe
    if isinstance(detalhe, Sequence) and not isinstance(detalhe, (str, bytes)):
        return "; ".join(str(x) for x in detalhe)
    if isinstance(detalhe, Mapping):
        interno = detalhe.get("message")
        if isinstance(interno, str):
            return interno
        numero = detalhe.get("number")
        if numero:
            return f"numero {numero}"
    return ""


def _e_problema_de_numero(detalhe: Any, texto: str) -> bool:
    """Distingue "numero sem WhatsApp" de um 400 qualquer.

    Quando o envio bate num numero inexistente, a Evolution devolve o proprio
    resultado da checagem como mensagem de erro: `{"jid": ..., "exists": false,
    "number": ...}`. Esse `exists: false` e a assinatura mais confiavel.
    """
    if isinstance(detalhe, Mapping) and "exists" in detalhe:
        return not detalhe.get("exists")
    minusculo = texto.lower()
    return "number" in minusculo or "invalid format" in minusculo
