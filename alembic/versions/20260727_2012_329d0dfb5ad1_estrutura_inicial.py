"""estrutura inicial

Cria as sete tabelas do sistema: usuario, conexao, campanha, lead, mensagem e as
duas listas globais por usuario (optout e ja_contatado).

Gerada por autogenerate e revisada a mao em dois pontos:

1. O `server_default` das colunas de data usa `sa.func.now()` no lugar do
   CURRENT_TIMESTAMP literal que o autogenerate escreveu. `func.now()` compila
   para `now()` no Postgres e para CURRENT_TIMESTAMP no SQLite, entao a mesma
   migracao serve ao banco de producao e a um banco descartavel de teste.
2. O JSONB da coluna `modelos` saiu com `astext_type=Text()`, sem o prefixo
   `sa.` - NameError na hora de rodar. Corrigido para `sa.Text()`.

Revision ID: 329d0dfb5ad1
Revises:
Create Date: 2026-07-27 20:12:32.136939
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "329d0dfb5ad1"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usuario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("senha_hash", sa.String(length=255), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usuario")),
        sa.UniqueConstraint("email", name=op.f("uq_usuario_email")),
    )

    op.create_table(
        "conexao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("nome_instancia", sa.String(length=100), nullable=False),
        sa.Column("numero", sa.String(length=20), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "desconectada",
                "aguardando_qr",
                "conectada",
                "banida",
                name="status_conexao",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="desconectada",
            nullable=False,
        ),
        sa.Column("conectada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuario.id"],
            name=op.f("fk_conexao_usuario_id_usuario"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conexao")),
        sa.UniqueConstraint("nome_instancia", name="uq_conexao_nome_instancia"),
    )
    op.create_index("ix_conexao_usuario_status", "conexao", ["usuario_id", "status"], unique=False)

    op.create_table(
        "optout",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("telefone", sa.String(length=20), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuario.id"],
            name=op.f("fk_optout_usuario_id_usuario"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_optout")),
        sa.UniqueConstraint("usuario_id", "telefone", name="uq_optout_usuario_telefone"),
    )

    op.create_table(
        "campanha",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("conexao_id", sa.Integer(), nullable=True),
        sa.Column("nome", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "rascunho",
                "rodando",
                "pausada",
                "concluida",
                name="status_campanha",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="rascunho",
            nullable=False,
        ),
        sa.Column(
            "modelos",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("teto_diario", sa.Integer(), server_default="40", nullable=False),
        sa.Column("intervalo_min_seg", sa.Integer(), server_default="120", nullable=False),
        sa.Column("intervalo_max_seg", sa.Integer(), server_default="300", nullable=False),
        sa.Column("hora_inicio", sa.Time(), server_default="09:00:00", nullable=False),
        sa.Column("hora_fim", sa.Time(), server_default="18:00:00", nullable=False),
        sa.Column("dias_uteis_apenas", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("respeitar_aquecimento", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("motivo_pausa", sa.Text(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "intervalo_max_seg >= intervalo_min_seg",
            name=op.f("ck_campanha_intervalo_coerente"),
        ),
        sa.CheckConstraint(
            "intervalo_min_seg > 0", name=op.f("ck_campanha_intervalo_min_positivo")
        ),
        sa.CheckConstraint("teto_diario > 0", name=op.f("ck_campanha_teto_positivo")),
        sa.ForeignKeyConstraint(
            ["conexao_id"],
            ["conexao.id"],
            name=op.f("fk_campanha_conexao_id_conexao"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuario.id"],
            name=op.f("fk_campanha_usuario_id_usuario"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campanha")),
    )
    op.create_index(
        "ix_campanha_conexao_status", "campanha", ["conexao_id", "status"], unique=False
    )
    op.create_index(
        "ix_campanha_usuario_status", "campanha", ["usuario_id", "status"], unique=False
    )

    op.create_table(
        "ja_contatado",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("telefone", sa.String(length=20), nullable=False),
        sa.Column("campanha_id", sa.Integer(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campanha_id"],
            ["campanha.id"],
            name=op.f("fk_ja_contatado_campanha_id_campanha"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuario.id"],
            name=op.f("fk_ja_contatado_usuario_id_usuario"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ja_contatado")),
        sa.UniqueConstraint("usuario_id", "telefone", name="uq_ja_contatado_usuario_telefone"),
    )
    op.create_index(
        op.f("ix_ja_contatado_campanha_id"), "ja_contatado", ["campanha_id"], unique=False
    )

    op.create_table(
        "lead",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campanha_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("telefone", sa.String(length=20), nullable=True),
        sa.Column("telefone_exibicao", sa.String(length=40), nullable=True),
        sa.Column("endereco", sa.Text(), nullable=True),
        sa.Column("categoria", sa.String(length=120), nullable=True),
        sa.Column("busca", sa.String(length=255), nullable=True),
        sa.Column("link_maps", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pendente",
                "checando",
                "sem_whatsapp",
                "enviado",
                "falhou",
                "respondeu",
                "optout",
                name="status_lead",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="pendente",
            nullable=False,
        ),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campanha_id"],
            ["campanha.id"],
            name=op.f("fk_lead_campanha_id_campanha"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lead")),
        sa.UniqueConstraint("campanha_id", "telefone", name="uq_lead_campanha_telefone"),
    )
    op.create_index("ix_lead_campanha_status", "lead", ["campanha_id", "status"], unique=False)

    op.create_table(
        "mensagem",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("campanha_id", sa.Integer(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column(
            "enviada_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "status_entrega",
            sa.Enum(
                "pendente",
                "enviada",
                "entregue",
                "lida",
                "falhou",
                "bloqueada",
                name="status_entrega",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="pendente",
            nullable=False,
        ),
        sa.Column("id_externo", sa.String(length=120), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["campanha_id"],
            ["campanha.id"],
            name=op.f("fk_mensagem_campanha_id_campanha"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["lead.id"],
            name=op.f("fk_mensagem_lead_id_lead"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mensagem")),
    )
    op.create_index(
        "ix_mensagem_campanha_enviada_em", "mensagem", ["campanha_id", "enviada_em"], unique=False
    )
    op.create_index(
        "ix_mensagem_campanha_status", "mensagem", ["campanha_id", "status_entrega"], unique=False
    )
    op.create_index("ix_mensagem_id_externo", "mensagem", ["id_externo"], unique=False)
    op.create_index(op.f("ix_mensagem_lead_id"), "mensagem", ["lead_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mensagem_lead_id"), table_name="mensagem")
    op.drop_index("ix_mensagem_id_externo", table_name="mensagem")
    op.drop_index("ix_mensagem_campanha_status", table_name="mensagem")
    op.drop_index("ix_mensagem_campanha_enviada_em", table_name="mensagem")
    op.drop_table("mensagem")
    op.drop_index("ix_lead_campanha_status", table_name="lead")
    op.drop_table("lead")
    op.drop_index(op.f("ix_ja_contatado_campanha_id"), table_name="ja_contatado")
    op.drop_table("ja_contatado")
    op.drop_index("ix_campanha_usuario_status", table_name="campanha")
    op.drop_index("ix_campanha_conexao_status", table_name="campanha")
    op.drop_table("campanha")
    op.drop_table("optout")
    op.drop_index("ix_conexao_usuario_status", table_name="conexao")
    op.drop_table("conexao")
    op.drop_table("usuario")
