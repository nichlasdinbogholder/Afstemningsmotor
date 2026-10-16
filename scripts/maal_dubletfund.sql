-- Måling af dubletfund for én kunde (kun læsning – ændrer intet).
-- Ret kundens id i første linje, og kør hele filen i TablePlus.
WITH k AS (SELECT 1404387 AS id),
seneste AS (
  SELECT max(r.koert_at) AS t FROM rule_runs r, k
  WHERE r.client_id = k.id AND r.rule_code = 'duplicate_entries'
),
-- Kunde/leverandør på bilagsniveau (står på en anden linje i samme bilag end omsætning/udgift).
bilag AS (
  SELECT e.bilagsnummer, e.dato, min(e.modpart) AS modpart, count(DISTINCT e.modpart) AS antal
  FROM entries e, k
  WHERE e.client_id = k.id AND e.modpart IS NOT NULL
  GROUP BY e.bilagsnummer, e.dato
),
f AS (
  SELECT f.id, f.severity, a.kontonummer AS konto, a.beloeb, a.client_id,
         a.dato AS a_dato, b.dato AS b_dato, a.bilagsnummer AS a_bilag, b.bilagsnummer AS b_bilag,
         ba.modpart AS a_mp, bb.modpart AS b_mp
  FROM findings f
  CROSS JOIN k CROSS JOIN seneste
  JOIN entries a ON a.id = f.entry_ids[1]
  JOIN entries b ON b.id = f.entry_ids[2]
  LEFT JOIN bilag ba ON ba.bilagsnummer = a.bilagsnummer AND ba.dato = a.dato
  LEFT JOIN bilag bb ON bb.bilagsnummer = b.bilagsnummer AND bb.dato = b.dato
  WHERE f.client_id = k.id AND f.rule_code = 'duplicate_entries'
    AND f.status = 'open' AND f.last_seen_at >= seneste.t
)
SELECT 1 AS nr, 'Aktuelle fund i alt' AS maaling, count(*) AS antal FROM f
UNION ALL SELECT 2, 'heraf high', count(*) FROM f WHERE severity = 'high'
UNION ALL SELECT 3, 'heraf medium', count(*) FROM f WHERE severity = 'medium'
UNION ALL SELECT 4, 'A. Bilagene har FORSKELLIG kunde/leverandør', count(*)
  FROM f WHERE a_mp IS NOT NULL AND b_mp IS NOT NULL AND a_mp <> b_mp
UNION ALL SELECT 5, 'B. Mindst ét af bilagene er tilbageført (modsat beløb, samme konto, ±3 dage)', count(*)
  FROM f WHERE EXISTS (
    SELECT 1 FROM entries c
    WHERE c.client_id = f.client_id AND c.kontonummer = f.konto AND c.beloeb = -f.beloeb
      AND c.dato BETWEEN least(f.a_dato, f.b_dato) - 3 AND greatest(f.a_dato, f.b_dato) + 3)
UNION ALL SELECT 6, 'C. Forskellige bilagspar (ét fund pr. bilagspar i stedet for pr. linje)',
  count(DISTINCT (least(a_bilag, b_bilag), greatest(a_bilag, b_bilag), least(a_dato, b_dato))) FROM f
UNION ALL SELECT 7, 'Tilbage efter A og B', count(*) FROM f
  WHERE NOT (a_mp IS NOT NULL AND b_mp IS NOT NULL AND a_mp <> b_mp)
    AND NOT EXISTS (
    SELECT 1 FROM entries c
    WHERE c.client_id = f.client_id AND c.kontonummer = f.konto AND c.beloeb = -f.beloeb
      AND c.dato BETWEEN least(f.a_dato, f.b_dato) - 3 AND greatest(f.a_dato, f.b_dato) + 3)
UNION ALL SELECT 8, 'Tilbage efter A og B, som bilagspar', count(DISTINCT (least(a_bilag, b_bilag), greatest(a_bilag, b_bilag), least(a_dato, b_dato)))
  FROM f
  WHERE NOT (a_mp IS NOT NULL AND b_mp IS NOT NULL AND a_mp <> b_mp)
    AND NOT EXISTS (
    SELECT 1 FROM entries c
    WHERE c.client_id = f.client_id AND c.kontonummer = f.konto AND c.beloeb = -f.beloeb
      AND c.dato BETWEEN least(f.a_dato, f.b_dato) - 3 AND greatest(f.a_dato, f.b_dato) + 3)
UNION ALL
SELECT * FROM (
  SELECT 100 + row_number() OVER (ORDER BY count(*) DESC)::int,
         'Konto ' || f.konto || ' ' || coalesce(max(acc.navn), '(navn ukendt)'), count(*)
  FROM f LEFT JOIN accounts acc ON acc.tenant_id = f.client_id AND acc.kontonummer = f.konto
  GROUP BY f.konto ORDER BY count(*) DESC LIMIT 15
) top
ORDER BY nr;
