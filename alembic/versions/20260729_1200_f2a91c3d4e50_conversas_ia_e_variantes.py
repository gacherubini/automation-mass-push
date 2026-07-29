"""conversas com IA e metricas de variantes

Revision ID: f2a91c3d4e50
Revises: 329d0dfb5ad1
Create Date: 2026-07-29 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a91c3d4e50"
down_revision: str | None = "329d0dfb5ad1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(nome: str, *valores: str) -> sa.Enum:
    return sa.Enum(
        *valores,
        name=nome,
        native_enum=False,
        create_constraint=True,
        length=32,
    )


def upgrade() -> None:
    op.add_column(
        "campanha",
        sa.Column(
            "modo_ia",
            _enum("modo_ia", "desligada", "rascunho", "automatica"),
            server_default="desligada",
            nullable=False,
        ),
    )
    op.add_column(
        "campanha",
        sa.Column("prompt_ia", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "campanha",
        sa.Column("limite_respostas_ia", sa.Integer(), server_default="4", nullable=False),
    )
    op.create_check_constraint(
        "limite_respostas_ia_valido",
        "campanha",
        "limite_respostas_ia > 0 AND limite_respostas_ia <= 20",
    )

    op.add_column(
        "mensagem", sa.Column("variante_indice", sa.Integer(), nullable=True)
    )
    op.add_column(
        "mensagem", sa.Column("variante_texto", sa.Text(), nullable=True)
    )

    op.create_table(
        "conversa",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "status_conversa",
                "aberta",
                "aguardando_humano",
                "encerrada",
                "erro",
            ),
            server_default="aberta",
            nullable=False,
        ),
        sa.Column(
            "papel_contato",
            _enum("papel_contato", "desconhecido", "atendente", "decisor"),
            server_default="desconhecido",
            nullable=False,
        ),
        sa.Column(
            "etapa",
            _enum(
                "etapa_conversa",
                "identificando",
                "buscando_decisor",
                "qualificando",
                "transferindo",
                "encerrada",
            ),
            server_default="identificando",
            nullable=False,
        ),
        sa.Column("resumo", sa.Text(), nullable=True),
        sa.Column("total_respostas_ia", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "criada_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "atualizada_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["lead.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", name="uq_conversa_lead_id"),
    )
    op.create_index(
        "ix_conversa_status_atualizada",
        "conversa",
        ["status", "atualizada_em"],
        unique=False,
    )

    op.create_table(
        "interacao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversa_id", sa.Integer(), nullable=False),
        sa.Column("origem_interacao_id", sa.Integer(), nullable=True),
        sa.Column(
            "autor",
            _enum("autor_interacao", "lead", "ia", "humano"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "status_interacao", "recebida", "rascunho", "enviada", "falhou"
            ),
            nullable=False,
        ),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("id_externo", sa.String(length=120), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "criada_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"], ["conversa.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origem_interacao_id"], ["interacao.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_externo", name="uq_interacao_id_externo"),
        sa.UniqueConstraint(
            "origem_interacao_id", name="uq_interacao_origem_interacao_id"
        ),
    )
    op.create_index(
        "ix_interacao_conversa_criada",
        "interacao",
        ["conversa_id", "criada_em"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_interacao_conversa_criada", table_name="interacao")
    op.drop_table("interacao")
    op.drop_index("ix_conversa_status_atualizada", table_name="conversa")
    op.drop_table("conversa")

    op.drop_column("mensagem", "variante_texto")
    op.drop_column("mensagem", "variante_indice")
    op.drop_constraint(
        op.f("ck_campanha_limite_respostas_ia_valido"),
        "campanha",
        type_="check",
    )
    op.drop_column("campanha", "limite_respostas_ia")
    op.drop_column("campanha", "prompt_ia")
    op.drop_column("campanha", "modo_ia")
