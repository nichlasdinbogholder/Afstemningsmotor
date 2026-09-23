# Projektregler
Dette er en afstemningsmotor til et dansk regnskabsfirma.
Bygherren er revisor, ikke programmør. Følg disse regler:
- Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16,
httpx, tenacity.
- Ingen nye biblioteker uden at spørge først og begrunde hvorfor.
- Skriv simpel, eksplicit kode. Ingen metaprogrammering, ingen
kloge abstraktioner.
- Kommentarer og fejlbeskeder på dansk.
- Al forretningslogik i Python/SQL, aldrig i frontenden.
- Systemet må ALDRIG skrive til e-conomic eller Dinero uden
eksplicit tilladelse i prompten. Læsning er altid tilladt.
- Alle API-nøgler læses fra miljøvariabler. Aldrig i koden,
aldrig i git.
- Efter hver ændring: giv en accepttest bygherren selv kan køre,
og forklar på dansk hvad han skal se.
- Ved tvivl om et krav: spørg. Gæt aldrig på forretningsregler
i regnskab.
