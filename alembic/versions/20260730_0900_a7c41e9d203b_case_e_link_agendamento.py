"""case real e link de agendamento por campanha

Revision ID: a7c41e9d203b
Revises: f2a91c3d4e50
Create Date: 2026-07-30 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c41e9d203b"
down_revision: str | None = "f2a91c3d4e50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NOVO_OBJETIVO = (
    "Apresente Gabriel como consultor de automacoes de IA para pequenos negocios. "
    "Confirme apenas se a pessoa e responsavel ou pode encaminhar o contato. "
    "Mostre o exemplo real cadastrado, relacione-o ao segmento sem inventar "
    "resultados e convide para uma conversa breve usando o link de agendamento. "
    "Nao investigue tarefas repetitivas, processos internos, dores ou problemas."
)


def upgrade() -> None:
    op.add_column(
        "campanha",
        sa.Column("case_ia", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "campanha",
        sa.Column("link_agendamento", sa.Text(), server_default="", nullable=False),
    )

    conexao = op.get_bind()
    conexao.execute(
        sa.text(
            "UPDATE campanha SET prompt_ia = :novo "
            "WHERE prompt_ia LIKE :objetivo_antigo"
        ),
        {
            "novo": NOVO_OBJETIVO,
            "objetivo_antigo": "%tarefa repetitiva%",
        },
    )
    # Uma campanha automatica antiga ainda nao tem case nem link. Voltar para
    # rascunho evita que a IA improvise enquanto o usuario preenche os campos.
    conexao.execute(
        sa.text(
            "UPDATE campanha SET modo_ia = 'rascunho' "
            "WHERE modo_ia = 'automatica'"
        )
    )


def downgrade() -> None:
    op.drop_column("campanha", "link_agendamento")
    op.drop_column("campanha", "case_ia")
