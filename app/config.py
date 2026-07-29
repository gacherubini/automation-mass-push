"""Configuracao lida do ambiente, com defaults que rodam local sem .env.

Nada de segredo escrito no codigo: o que e sensivel (SECRET_KEY, chave da
Evolution) tem default so para o desenvolvedor conseguir subir a aplicacao na
primeira tentativa, e `avisos_de_producao()` denuncia quando esse default
sobreviveu ate producao.

A leitura acontece uma vez, em `configuracao()`, que e cacheada. O resto do
sistema importa a funcao, nao o objeto, para os testes conseguirem limpar o
cache e trocar o ambiente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Default de desenvolvimento. Bate com o docker-compose deste repositorio, para
# `docker compose up` e `alembic upgrade head` funcionarem sem configurar nada.
#
# O driver e o psycopg 3 ("+psycopg"), nao o psycopg2: e o que esta no
# requirements.txt. Sem o sufixo, o SQLAlchemy procura psycopg2 e quebra.
URL_BANCO_PADRAO = "postgresql+psycopg://postgres:postgres@localhost:5432/mass_push"

SECRET_KEY_PADRAO = "dev-inseguro-troque-em-producao"
CHAVE_EVOLUTION_PADRAO = "dev-inseguro-troque-em-producao"

# Raiz do repositorio (app/config.py -> app/ -> raiz). O .env mora aqui.
_RAIZ = Path(__file__).resolve().parent.parent


def _carregar_dotenv() -> None:
    """Le `.env` da raiz se existir, sem sobrescrever variavel ja exportada.

    O Docker Compose carrega o arquivo sozinho para os containers; o processo
    local (uvicorn, alembic, pytest avulso) nao. Sem isto, a app usa o default
    `postgres:postgres` enquanto o container foi criado com a senha do .env.
    """
    caminho = _RAIZ / ".env"
    if not caminho.is_file():
        return
    try:
        texto = caminho.read_text(encoding="utf-8")
    except OSError:
        return
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        nome, _, valor = linha.partition("=")
        nome = nome.strip()
        if not nome or nome in os.environ:
            continue
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        os.environ[nome] = valor


_carregar_dotenv()


def _texto(nome: str, padrao: str) -> str:
    # Variavel definida como string vazia conta como ausente: no docker-compose
    # e facil deixar `EVOLUTION_API_KEY=` sem valor, e um vazio silencioso e
    # pior que o default.
    valor = os.getenv(nome, "").strip()
    return valor or padrao


def _inteiro(nome: str, padrao: int) -> int:
    bruto = os.getenv(nome, "").strip()
    if not bruto:
        return padrao
    try:
        return int(bruto)
    except ValueError:
        # Configuracao malformada nao pode virar comportamento silencioso.
        raise ValueError(f"{nome} precisa ser um numero inteiro, veio {bruto!r}")


def _booleano(nome: str, padrao: bool) -> bool:
    bruto = os.getenv(nome, "").strip().lower()
    if not bruto:
        return padrao
    return bruto in {"1", "true", "sim", "yes", "on"}


@dataclass(frozen=True)
class Configuracao:
    """Tudo que muda entre a maquina do dev e o servidor."""

    database_url: str = URL_BANCO_PADRAO
    secret_key: str = SECRET_KEY_PADRAO

    # Evolution API: a URL e vista de dentro da rede do docker-compose, onde o
    # servico se chama "evolution-api". Rodando a aplicacao fora do compose,
    # aponte para localhost na porta publicada.
    evolution_url: str = "http://localhost:8080"
    evolution_api_key: str = CHAVE_EVOLUTION_PADRAO

    # Conversa com IA. Sem chave, o recurso fica indisponivel e o restante da
    # aplicacao continua funcionando normalmente.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # `echo` do SQLAlchemy. Util para ver a query que o ritmo dispara; barulhento
    # demais para deixar ligado.
    debug_sql: bool = False

    # Reciclagem de conexao. O Postgres em container costuma derrubar conexao
    # ociosa sem avisar, e a conexao morta so aparece na proxima query. Trocar de
    # tempos em tempos e mais barato que descobrir isso em producao.
    pool_size: int = 5
    pool_max_overflow: int = 10
    pool_reciclar_seg: int = 1800

    ambiente: str = "desenvolvimento"

    avisos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def producao(self) -> bool:
        return self.ambiente.lower().startswith("prod")

    @property
    def gemini_disponivel(self) -> bool:
        return bool(self.gemini_api_key)


def _avisos_de_producao(
    ambiente: str, secret_key: str, evolution_api_key: str
) -> tuple[str, ...]:
    """Defaults inseguros que sobreviveram ate producao.

    Nao levanta excecao aqui: quem decide se isso e fatal e quem sobe a
    aplicacao. Aqui so garantimos que o problema fica visivel.
    """
    if not ambiente.lower().startswith("prod"):
        return ()

    alertas: list[str] = []
    if secret_key == SECRET_KEY_PADRAO:
        alertas.append(
            "SECRET_KEY ainda e o valor de desenvolvimento. Qualquer um consegue "
            "forjar sessao de qualquer usuario."
        )
    if evolution_api_key == CHAVE_EVOLUTION_PADRAO:
        alertas.append(
            "EVOLUTION_API_KEY ainda e o valor de desenvolvimento. Quem alcancar "
            "a porta da Evolution controla os WhatsApps conectados."
        )
    return tuple(alertas)


@lru_cache(maxsize=1)
def configuracao() -> Configuracao:
    """Le o ambiente uma unica vez por processo."""
    ambiente = _texto("AMBIENTE", "desenvolvimento")
    secret_key = _texto("SECRET_KEY", SECRET_KEY_PADRAO)
    evolution_api_key = _texto("EVOLUTION_API_KEY", CHAVE_EVOLUTION_PADRAO)

    return Configuracao(
        database_url=_texto("DATABASE_URL", URL_BANCO_PADRAO),
        secret_key=secret_key,
        evolution_url=_texto("EVOLUTION_URL", "http://localhost:8080").rstrip("/"),
        evolution_api_key=evolution_api_key,
        gemini_api_key=_texto("GEMINI_API_KEY", ""),
        gemini_model=_texto("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        gemini_url=_texto(
            "GEMINI_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        ).rstrip("/"),
        debug_sql=_booleano("DEBUG_SQL", False),
        pool_size=_inteiro("POOL_SIZE", 5),
        pool_max_overflow=_inteiro("POOL_MAX_OVERFLOW", 10),
        pool_reciclar_seg=_inteiro("POOL_RECICLAR_SEG", 1800),
        ambiente=ambiente,
        avisos=_avisos_de_producao(ambiente, secret_key, evolution_api_key),
    )


def recarregar() -> Configuracao:
    """Relê o ambiente. Existe para os testes; em producao nada chama isso."""
    configuracao.cache_clear()
    return configuracao()
