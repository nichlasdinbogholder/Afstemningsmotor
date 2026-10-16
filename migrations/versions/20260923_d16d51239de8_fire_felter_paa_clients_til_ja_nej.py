"""Fire felter på clients til ja/nej

sagsstyring, betalinger, bilagshaandtering og debitorstyring ændres fra fri
tekst til ja/nej. Eksisterende tekst oversættes (fx "ja" -> ja, "nej"/tom -> nej).
Kan en værdi ikke oversættes sikkert, stopper migreringen uden at ændre noget.

Revision ID: d16d51239de8
Revises: d86b8867ada4
Create Date: 2026-09-23 19:23:24.790777

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd16d51239de8'
down_revision: Union[str, Sequence[str], None] = 'd86b8867ada4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FELTER = ("sagsstyring", "betalinger", "bilagshaandtering", "debitorstyring")

# Tekst, der tolkes som ja/nej (små bogstaver, uden mellemrum rundt om).
JA = ("ja", "j", "yes", "y", "true", "sand", "1", "x")
NEJ = ("nej", "n", "no", "false", "falsk", "0", "")


def _liste(vaerdier: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in vaerdier)


def upgrade() -> None:
    """Opgrader databasen."""
    forbindelse = op.get_bind()
    for felt in FELTER:
        uklare = forbindelse.execute(sa.text(
            f"SELECT count(*) FROM clients WHERE {felt} IS NOT NULL "
            f"AND lower(trim({felt})) NOT IN ({_liste(JA + NEJ)})"
        )).scalar()
        if uklare:
            raise RuntimeError(
                f"{uklare} kunde(r) har en tekst i '{felt}', der ikke kan oversættes "
                "til ja/nej. Ret dem til 'ja' eller 'nej' først – intet er ændret."
            )

    for felt in FELTER:
        op.alter_column(
            "clients", felt,
            existing_type=sa.Text(),
            type_=sa.Boolean(),
            postgresql_using=f"coalesce(lower(trim({felt})) IN ({_liste(JA)}), false)",
            server_default=sa.text("false"),
            nullable=False,
        )


def downgrade() -> None:
    """Nedgrader databasen (ja/nej bliver til teksten 'ja'/'nej')."""
    for felt in FELTER:
        op.alter_column(
            "clients", felt,
            existing_type=sa.Boolean(),
            type_=sa.Text(),
            postgresql_using=f"CASE WHEN {felt} THEN 'ja' ELSE 'nej' END",
            server_default=None,
            nullable=True,
        )
