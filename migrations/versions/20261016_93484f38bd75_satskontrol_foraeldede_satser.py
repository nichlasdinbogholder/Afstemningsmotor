"""Fagassistent fase 3, trin 11: udfaldet 'stale' (sats ikke bekræftet i lang tid) i kb_rate_checks

Revision ID: 93484f38bd75
Revises: 4ba5848646a7
Create Date: 2026-10-16 12:10:05.231387

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93484f38bd75'
down_revision: Union[str, Sequence[str], None] = '4ba5848646a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Opgrader databasen."""
    # Alembic opdager ikke ændrede CHECK-regler – derfor i hånden.
    op.drop_constraint("outcome_gyldig", "kb_rate_checks", type_="check")
    op.create_check_constraint("outcome_gyldig", "kb_rate_checks",
                               "outcome IN ('unchanged', 'changed', 'failed', 'stale')")
    op.drop_constraint("afgoerelse_passer_udfald", "kb_rate_checks", type_="check")
    op.create_check_constraint(
        "afgoerelse_passer_udfald", "kb_rate_checks",
        "decision IS NULL OR (decision IN ('approved', 'rejected') AND outcome = 'changed') "
        "OR (decision = 'noted' AND outcome IN ('failed', 'stale'))")


def downgrade() -> None:
    """Nedgrader databasen."""
    op.drop_constraint("afgoerelse_passer_udfald", "kb_rate_checks", type_="check")
    op.create_check_constraint(
        "afgoerelse_passer_udfald", "kb_rate_checks",
        "decision IS NULL OR (decision IN ('approved', 'rejected') AND outcome = 'changed') "
        "OR (decision = 'noted' AND outcome = 'failed')")
    op.drop_constraint("outcome_gyldig", "kb_rate_checks", type_="check")
    op.create_check_constraint("outcome_gyldig", "kb_rate_checks", "outcome IN ('unchanged', 'changed', 'failed')")
