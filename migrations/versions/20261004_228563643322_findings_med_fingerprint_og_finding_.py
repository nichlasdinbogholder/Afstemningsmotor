"""findings med fingerprint og finding_events

1. Den gamle tabel findings (de tre regler for åbne poster) OMDØBES til
   aabne_post_fund – med alle data, nøgler og regler. Funktionerne regel_1..3
   og luk_loeste_fund genskabes, så de skriver i den omdøbte tabel.
2. Ny tabel findings: ét fund pr. (kunde, regel, fingerprint). Status ændres kun
   af et menneske.
3. Ny tabel finding_events: triggere skriver en række ved oprettelse og ved hver
   statusændring. En statusændring uden actor afvises. Fund og hændelser kan
   ikke slettes, og hændelser kan ikke ændres.

Revision ID: 228563643322
Revises: 572d6002a99c
Create Date: 2026-10-04 19:16:08.067773

"""
from typing import Sequence, Union

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '228563643322'
down_revision: Union[str, Sequence[str], None] = '572d6002a99c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GAMLE_FUNKTIONER = ("regel_1_smaa_restbeloeb", "regel_2_betaling_uden_faktura",
                    "regel_3_forfalden_over_6_mdr", "luk_loeste_fund")

OMDOEB = [  # (gammelt navn, nyt navn) for nøgler og regler på den gamle tabel
    ("pk_findings", "pk_aabne_post_fund"),
    ("fk_findings_client_id_clients", "fk_aabne_post_fund_client_id_clients"),
    ("uq_findings_kunde_regel_post", "uq_aabne_post_fund_kunde_regel_post"),
    ("ck_findings_loest_har_tidspunkt", "ck_aabne_post_fund_loest_har_tidspunkt"),
    ("ck_findings_regel_gyldig", "ck_aabne_post_fund_regel_gyldig"),
    ("ck_findings_status_gyldig", "ck_aabne_post_fund_status_gyldig"),
]

TRIGGERE = """
CREATE FUNCTION finding_oprettet_logges() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO finding_events (finding_id, from_status, to_status, actor, note)
    VALUES (NEW.id, NULL, NEW.status,
            coalesce(nullif(current_setting('afstemning.actor', true), ''), 'system'),
            'Fund oprettet');
    RETURN NULL;
END $$;

CREATE FUNCTION finding_status_logges() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_actor text := nullif(current_setting('afstemning.actor', true), '');
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF v_actor IS NULL THEN
            RAISE EXCEPTION 'Status på fund % kan kun ændres med en actor (brug app.rules.status.saet_status)', OLD.id;
        END IF;
        INSERT INTO finding_events (finding_id, from_status, to_status, actor, note)
        VALUES (OLD.id, OLD.status, NEW.status, v_actor,
                nullif(current_setting('afstemning.note', true), ''));
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION fund_kan_ikke_slettes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% kan ikke slettes (historik)', TG_TABLE_NAME;
END $$;

CREATE FUNCTION finding_events_kan_ikke_aendres() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'finding_events kan ikke ændres eller slettes';
END $$;

CREATE TRIGGER findings_oprettet AFTER INSERT ON findings
    FOR EACH ROW EXECUTE FUNCTION finding_oprettet_logges();
CREATE TRIGGER findings_status AFTER UPDATE OF status ON findings
    FOR EACH ROW EXECUTE FUNCTION finding_status_logges();
CREATE TRIGGER findings_slettes_ikke BEFORE DELETE ON findings
    FOR EACH ROW EXECUTE FUNCTION fund_kan_ikke_slettes();
CREATE TRIGGER finding_events_kun_tilfoej BEFORE UPDATE OR DELETE ON finding_events
    FOR EACH ROW EXECUTE FUNCTION finding_events_kan_ikke_aendres();
"""


def _genskab_funktioner(fra: str, til: str) -> None:
    """Genskab de gamle regel-funktioner, så de peger på tabellen `til` i stedet for `fra`."""
    forbindelse = op.get_bind()
    for navn in GAMLE_FUNKTIONER:
        definition = forbindelse.execute(
            sa.text("SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = :n"),
            {"n": navn},
        ).scalar_one()
        op.execute(re.sub(rf"\b{fra}\b", til, definition))


def upgrade() -> None:
    """Opgrader databasen."""
    # 1. Gammel tabel omdøbes – data bevares.
    op.rename_table("findings", "aabne_post_fund")
    for gammel, ny in OMDOEB:
        op.execute(f"ALTER TABLE aabne_post_fund RENAME CONSTRAINT {gammel} TO {ny}")
    op.execute("ALTER INDEX ix_findings_status RENAME TO ix_aabne_post_fund_status")
    op.execute("ALTER SEQUENCE findings_id_seq RENAME TO aabne_post_fund_id_seq")
    _genskab_funktioner("findings", "aabne_post_fund")

    # 2. Ny findings.
    op.create_table('findings',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('rule_code', sa.Text(), nullable=False),
    sa.Column('rule_version', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('fingerprint', sa.Text(), nullable=False),
    sa.Column('severity', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='open', nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('entry_ids', postgresql.ARRAY(sa.BigInteger()), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=True),
    sa.Column('period_end', sa.Date(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("rule_code ~ '^[a-z0-9_]+$'", name=op.f('ck_findings_rule_code_gyldig')),
    sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name=op.f('ck_findings_severity_gyldig')),
    sa.CheckConstraint("status IN ('open', 'accepted', 'resolved', 'ignored')", name=op.f('ck_findings_status_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_findings_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_findings')),
    sa.UniqueConstraint('client_id', 'rule_code', 'fingerprint', name='uq_findings_kunde_regel_fingerprint')
    )
    op.create_index('ix_findings_kunde_sidst_set', 'findings', ['client_id', 'last_seen_at'], unique=False)
    op.create_index('ix_findings_kunde_status_alvor', 'findings', ['client_id', 'status', 'severity'], unique=False)

    # 3. Loggen over statusændringer.
    op.create_table('finding_events',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('finding_id', sa.BigInteger(), nullable=False),
    sa.Column('from_status', sa.Text(), nullable=True),
    sa.Column('to_status', sa.Text(), nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("from_status IN ('open', 'accepted', 'resolved', 'ignored')", name=op.f('ck_finding_events_from_status_gyldig')),
    sa.CheckConstraint("to_status IN ('open', 'accepted', 'resolved', 'ignored')", name=op.f('ck_finding_events_to_status_gyldig')),
    sa.ForeignKeyConstraint(['finding_id'], ['findings.id'], name=op.f('fk_finding_events_finding_id_findings'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_finding_events'))
    )
    op.create_index(op.f('ix_finding_events_finding_id'), 'finding_events', ['finding_id'], unique=False)
    op.execute(TRIGGERE)


def downgrade() -> None:
    """Nedgrader databasen. OBS: nye fund og deres hændelser går tabt."""
    op.drop_table('finding_events')
    op.drop_table('findings')
    for navn in ("finding_oprettet_logges", "finding_status_logges", "fund_kan_ikke_slettes",
                 "finding_events_kan_ikke_aendres"):
        op.execute(f"DROP FUNCTION {navn}()")
    op.execute("ALTER SEQUENCE aabne_post_fund_id_seq RENAME TO findings_id_seq")
    op.execute("ALTER INDEX ix_aabne_post_fund_status RENAME TO ix_findings_status")
    for gammel, ny in OMDOEB:
        op.execute(f"ALTER TABLE aabne_post_fund RENAME CONSTRAINT {ny} TO {gammel}")
    op.rename_table("aabne_post_fund", "findings")
    _genskab_funktioner("aabne_post_fund", "findings")
