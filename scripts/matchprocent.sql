-- Matchprocent pr. kunde: hvor stor en del af kontoudtogenes linjer er fundet i bogføringen?
-- Kun læsning. Tallene er fra seneste regelkørsel (natkørslen eller run-rules).
SELECT c.kundenummer,
       c.navn,
       count(DISTINCT s.id)                                                   AS kontoudtog,
       count(l.id)                                                            AS linjer,
       count(l.id) FILTER (WHERE l.match_trin IS NOT NULL)                    AS matchet,
       count(l.id) FILTER (WHERE l.match_trin = 'bilag_beloeb')               AS trin1_bilag_beloeb,
       count(l.id) FILTER (WHERE l.match_trin = 'beloeb_dato')                AS trin2_beloeb_dato,
       count(l.id) FILTER (WHERE l.match_trin IS NULL)                        AS mangler_i_bogfoering,
       round(100.0 * count(l.id) FILTER (WHERE l.match_trin IS NOT NULL)
                   / nullif(count(l.id), 0), 1)                               AS matchprocent
FROM clients c
JOIN statements s       ON s.client_id = c.id
JOIN statement_lines l  ON l.statement_id = s.id
GROUP BY c.id, c.kundenummer, c.navn
ORDER BY matchprocent NULLS FIRST, c.kundenummer;
