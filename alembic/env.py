"""Ambiente do Alembic.

A URL do banco vem de `app.config`, nunca do alembic.ini: assim `alembic upgrade
head` e a aplicacao leem exatamente a mesma DATABASE_URL, e nenhuma senha entra
no repositorio.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import configuracao
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `autogenerate` compara o banco com este metadata. Importar app.models acima ja
# registrou todas as tabelas nele.
target_metadata = Base.metadata

# A URL vai para o config em memoria; o arquivo .ini continua sem ela.
config.set_main_option("sqlalchemy.url", configuracao().database_url.replace("%", "%%"))


def _opcoes_comuns() -> dict:
    return {
        "target_metadata": target_metadata,
        # Sem isto o autogenerate ignora mudanca de VARCHAR(20) para VARCHAR(40)
        # e a coluna fica com o tamanho antigo em producao.
        "compare_type": True,
        "compare_server_default": True,
        # Constraint sem nome nao pode ser removida por uma migracao futura. A
        # convencao vive em app.models e precisa valer aqui tambem.
        "render_as_batch": False,
    }


def migrar_offline() -> None:
    """Gera o SQL sem conectar, para revisar ou aplicar a mao (`--sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_opcoes_comuns(),
    )

    with context.begin_transaction():
        context.run_migrations()


def migrar_online() -> None:
    conectavel = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with conectavel.connect() as conexao:
        context.configure(connection=conexao, **_opcoes_comuns())

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    migrar_offline()
else:
    migrar_online()
