"""Produktive timer kun for fastlønnede – timelønnede har ingen (bygherren 26.10.2026).

Revision ID: c41d8e2f9a63
Revises: a7c3e19d4b20
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op

revision = "c41d8e2f9a63"
down_revision = "a7c3e19d4b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("time_employee_rates", "productive_hours_year", existing_type=sa.Numeric(7, 2), nullable=True)
    op.drop_constraint("produktive_timer_positive", "time_employee_rates", type_="check")
    op.create_check_constraint("produktive_timer_positive", "time_employee_rates",
                               "productive_hours_year IS NULL OR productive_hours_year > 0")
    # Timelønnedes satser har ingen produktive timer – kostprisen tager udgangspunkt i timelønnen.
    # Ændringen logges af time_audit-triggeren (uden aktør, da den laves af migreringen).
    op.execute("""
        UPDATE time_employee_rates r SET productive_hours_year = NULL
        FROM time_employees e
        WHERE e.id = r.employee_id AND e.employment = 'timeloennet' AND r.productive_hours_year IS NOT NULL
    """)


def downgrade() -> None:
    forbindelse = op.get_bind()
    if forbindelse.execute(sa.text(
            "SELECT count(*) FROM time_employee_rates WHERE productive_hours_year IS NULL")).scalar():
        raise RuntimeError("Kan ikke fortrydes: der findes satser uden produktive timer (timelønnede)")
    op.drop_constraint("produktive_timer_positive", "time_employee_rates", type_="check")
    op.create_check_constraint("produktive_timer_positive", "time_employee_rates", "productive_hours_year > 0")
    op.alter_column("time_employee_rates", "productive_hours_year", existing_type=sa.Numeric(7, 2), nullable=False)
