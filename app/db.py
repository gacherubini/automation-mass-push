"""Engine, sessao e a dependencia que o FastAPI injeta nas rotas.

O engine e criado uma vez por processo (`lru_cache`) porque ele carrega o pool
de conexoes: criar um por requisicao abriria conexao nova a cada chamada e
esgotaria o Postgres num dia de disparo.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import configuracao
from app.models import Base

__all__ = ["Base", "criar_tabelas", "get_sessao", "engine", "fabrica_de_sessao", "sessao"]


@lru_cache(maxsize=1)
def engine() -> Engine:
    cfg = configuracao()

    parametros = {
        "echo": cfg.debug_sql,
        # Testa a conexao antes de entregar. Sem isso, a primeira query depois
        # de o Postgres reciclar a conexao ociosa estoura com "server closed the
        # connection unexpectedly" - e o disparador fica ocioso boa parte do dia.
        "pool_pre_ping": True,
    }

    # SQLite (testes, prototipo) nao tem pool configuravel do mesmo jeito;
    # passar pool_size ali levanta TypeError.
    if not cfg.database_url.startswith("sqlite"):
        parametros.update(
            pool_size=cfg.pool_size,
            max_overflow=cfg.pool_max_overflow,
            pool_recycle=cfg.pool_reciclar_seg,
        )

    return create_engine(cfg.database_url, **parametros)


@lru_cache(maxsize=1)
def fabrica_de_sessao() -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine(),
        # Sem expire_on_commit, o objeto continua legivel depois do commit. Com
        # ele ligado, ler `campanha.nome` depois de salvar dispara um SELECT novo
        # - as vezes fora do escopo da sessao, que ai ja fechou.
        expire_on_commit=False,
        autoflush=False,
    )


def sessao() -> Session:
    """Sessao avulsa, para script e worker de disparo. Quem abre, fecha."""
    return fabrica_de_sessao()()


def get_sessao() -> Iterator[Session]:
    """Dependencia do FastAPI: `sessao: Session = Depends(get_sessao)`.

    Sem commit automatico: quem escreve declara o commit. Rollback em excecao e
    obrigatorio para a conexao nao voltar suja para o pool.
    """
    with fabrica_de_sessao()() as sess:
        try:
            yield sess
        except Exception:
            sess.rollback()
            raise


def criar_tabelas() -> None:
    """Atalho para teste e prototipo. Em producao quem cria tabela e o Alembic."""
    Base.metadata.create_all(engine())


def reiniciar() -> None:
    """Descarta engine e pool. Serve para o teste trocar a URL do banco."""
    if engine.cache_info().currsize:
        engine().dispose()
    engine.cache_clear()
    fabrica_de_sessao.cache_clear()
