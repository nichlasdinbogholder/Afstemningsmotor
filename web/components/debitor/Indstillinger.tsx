"use client";

import { useEffect, useState } from "react";
import { useMig } from "@/components/Ramme";
import { hent, send } from "@/lib/api";
import { beloeb, dato, RYKKERTILSTAND } from "@/lib/format";

type Data = {
  virksomhed: { navn: string; cvr: string | null; adresse: string | null; postnr: string | null; by: string | null; kundenummer: string };
  svar_email: string | null; kompensation_paa_rykker: number; paamindelse_efter_dage: number | null; rykkergebyr: string; kompensationsbeloeb: string;
  rentesats: string | null; inkasso_efter_dage: number; rykkere: string; minimum: string;
  foerste_rykker_efter_dage: number; dage_mellem_rykkere: number; fordeling: string; erhvervsgrupper: number[];
  fi_kreditornummer: string | null; gebyrkonto: number | null; rentekonto: number | null; gebyraftale: string | null;
  inkassomandat: string | null; automatisk_inkasso: boolean; inkasso_minimum: string;
};

// Et felt, der kan rettes: navn i API'et, tekst og type.
type Felt = { felt: keyof Data & string; tekst: string; type: "tekst" | "tal" | "beloeb" | "valg" | "grupper"; valg?: [string, string][] };

export default function Indstillinger({ kundeId, aendret }: { kundeId: number; aendret?: (d: Data) => void }) {
  const [d, setD] = useState<Data | null>(null);
  const admin = useMig()?.rolle === "admin";
  useEffect(() => { hent<Data>(`/api/opkraevning/${kundeId}/indstillinger`).then(setD); }, [kundeId]);
  if (!d) return <p className="text-slate-500">Henter …</p>;
  const kortProps = { d, kundeId, admin, gemt: (ny: Data) => { setD(ny); aendret?.(ny); } };
  const v = d.virksomhed;

  return (
    <div className="space-y-8 text-sm">
      <Sektion titel="Generelt">
        <div className="grid gap-4 lg:grid-cols-3">
          <Kort titel="Virksomhedsoplysninger" ok {...kortProps}>
            <p>CVR-nr. {v.cvr ?? "–"}</p><p>Navn: {v.navn}</p>
            {v.adresse && <p>{v.adresse}</p>}{(v.postnr || v.by) && <p>{v.postnr} {v.by}</p>}
            <p className="mt-1 text-xs opacity-75">Rettes i kundekartoteket.</p>
          </Kort>
          <Kort titel="Rykker-forsendelse" ok={!!d.svar_email} {...kortProps}
                felter={[{ felt: "svar_email", tekst: "Svar går til (reply-to)", type: "tekst" }]}>
            <p>Afsender: rykker@dinbogholder.dk</p>
            <p>Svar går til (reply-to): {d.svar_email ?? <b>mangler – der kan ikke sendes rykkere</b>}</p>
            <p className="mt-1 text-xs opacity-75">Debitorens svar går direkte til kunden – aldrig til os.</p>
          </Kort>
          <Kort titel="Betalinger" ok={!!d.fi_kreditornummer} {...kortProps}
                felter={[{ felt: "fi_kreditornummer", tekst: "FI-kreditornummer (8 cifre)", type: "tekst" }]}>
            <p>FI-kreditornr.: {d.fi_kreditornummer ?? <b>mangler</b>}</p>
            <p>Indbetalinger fordeles: {d.fordeling === "costs_first" ? "gebyrer og renter først" : "hovedstol først"}</p>
            <p className="mt-1 text-xs opacity-75">Debitoren betaler altid til kundens egen konto.</p>
          </Kort>
        </div>
      </Sektion>

      <Sektion titel="Rykkere">
        <Kort titel="Rykkerprocedure" ok neutral {...kortProps}
              felter={[
                { felt: "rykkere", tekst: "Rykkere", type: "valg", valg: [["off", "Slået fra"], ["preview", "Prøvekørsel"]] },
                { felt: "paamindelse_efter_dage", tekst: "Venlig påmindelse: dage efter forfald (tom = ingen påmindelse)", type: "tal" },
                { felt: "foerste_rykker_efter_dage", tekst: "Rykker 1 uden påmindelse: dage efter forfald", type: "tal" },
                { felt: "dage_mellem_rykkere", tekst: "Dage mellem rykkerne (mindst 10)", type: "tal" },
                { felt: "kompensation_paa_rykker", tekst: "Kompensationsgebyr på rykker", type: "valg", valg: [["1", "1"], ["2", "2"], ["3", "3"]] },
              ]}>
          <p className="mb-2">Rykkere: <b>{RYKKERTILSTAND[d.rykkere] ?? d.rykkere}</b></p>
          <table className="w-full bg-white/60">
            <thead className="text-left text-xs font-semibold"><tr><th className="px-2 py-1">Rykker</th><th className="px-2 py-1">Antal dage</th>
              <th className="px-2 py-1">Kompensationsgebyr (kun erhverv)</th><th className="px-2 py-1 text-right">Gebyr</th></tr></thead>
            <tbody>
              {d.paamindelse_efter_dage !== null && (
                <tr className="border-t border-sky-100">
                  <td className="px-2 py-1">Venlig påmindelse</td>
                  <td className="px-2 py-1">{d.paamindelse_efter_dage} efter forfald</td>
                  <td className="px-2 py-1" /><td className="px-2 py-1 text-right">uden gebyr</td>
                </tr>
              )}
              {[1, 2, 3].map((n) => (
                <tr key={n} className="border-t border-sky-100">
                  <td className="px-2 py-1">{n}</td>
                  <td className="px-2 py-1">{n > 1 ? `${d.dage_mellem_rykkere} efter forrige`
                    : d.paamindelse_efter_dage !== null ? `${d.dage_mellem_rykkere} efter påmindelsen`
                    : `${d.foerste_rykker_efter_dage} efter forfald`}</td>
                  <td className="px-2 py-1">{n === d.kompensation_paa_rykker ? `${beloeb(d.kompensationsbeloeb)} kr.` : ""}</td>
                  <td className="px-2 py-1 text-right">{beloeb(d.rykkergebyr)} kr.</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs opacity-75">Lovens grænser kan ikke ændres: gebyr højst 100 kr., højst 3 rykkere, mindst 10 dage imellem.</p>
        </Kort>
      </Sektion>

      <Sektion titel="Inkasso">
        <Kort titel="Inkasso" ok={!!d.inkassomandat} {...kortProps}>
          <Linje k="Inkassofirma" v="Inkasso Mægleren (overdrages manuelt fra en liste)" />
          <Linje k="Antal dage til inkasso" v={`${d.inkasso_efter_dage} (efter sidste rykker)`} />
          <Linje k="Inkassomandat" v={d.inkassomandat ? `underskrevet ${dato(d.inkassomandat)}` : "mangler – ingen inkassosager"} />
          <Linje k="Minimum for inkasso" v={`${beloeb(d.inkasso_minimum)} kr.`} />
          <Linje k="Automatisk overdragelse" v={d.automatisk_inkasso ? "ja" : "nej – godkendes af en medarbejder"} />
        </Kort>
      </Sektion>

      <Sektion titel="Andre indstillinger">
        <Kort titel="Andre indstillinger" ok neutral {...kortProps}
              felter={[
                { felt: "minimum", tekst: "Behandl som betalt, hvis restbeløbet er under (kr.)", type: "beloeb" },
                { felt: "erhvervsgrupper", tekst: "Debitorgrupper, der er erhverv (fx 2, 3)", type: "grupper" },
                { felt: "gebyrkonto", tekst: "Konto for rykkergebyrer", type: "tal" },
                { felt: "rentekonto", tekst: "Konto for renter", type: "tal" },
              ]}>
          <Linje k="Behandl som betalt, hvis restbeløbet er under" v={`${beloeb(d.minimum)} kr.`} />
          <Linje k="Stands rykkerprocessen, når hovedstolen er betalt" v="Ja (altid)" />
          <Linje k="Renter" v={d.rentesats ? `${beloeb(d.rentesats)} % (referencesatsen + 8)` : "referencesatsen for halvåret mangler"} />
          <Linje k="Debitorgrupper, der er erhverv" v={d.erhvervsgrupper.join(", ") || "ingen – alle behandles som private"} />
          <Linje k="Konto for rykkergebyrer / renter" v={`${d.gebyrkonto ?? "mangler"} / ${d.rentekonto ?? "mangler"}`} />
          <Linje k="Gebyraftale med kunden" v={d.gebyraftale ? `underskrevet ${dato(d.gebyraftale)}` : "mangler – gebyrer kan ikke faktureres"} />
        </Kort>
      </Sektion>
      {!admin && <p className="text-xs text-slate-500">Kun administratorer kan rette indstillingerne. Alle ændringer logges.</p>}
    </div>
  );
}

function Sektion({ titel, children }: { titel: string; children: React.ReactNode }) {
  return <section><h2 className="mb-3 border-b border-slate-200 pb-1 italic text-slate-600">{titel}</h2>{children}</section>;
}

function Linje({ k, v }: { k: string; v: React.ReactNode }) {
  return <p className="flex justify-between gap-4 border-b border-black/5 py-1 last:border-0"><span>{k}</span><span className="text-right">{v}</span></p>;
}

function Kort({ titel, ok, neutral, felter, d, kundeId, admin, gemt, children }: {
  titel: string; ok: boolean; neutral?: boolean; felter?: Felt[]; d: Data; kundeId: number; admin: boolean;
  gemt: (d: Data) => void; children: React.ReactNode;
}) {
  const [redigerer, setRedigerer] = useState(false);
  const farve = neutral ? "border-sky-200 bg-sky-50 text-sky-950" : ok ? "border-emerald-200 bg-emerald-50 text-emerald-950" : "border-red-200 bg-red-50 text-red-900";
  return (
    <div className={`rounded-lg border p-4 ${farve}`}>
      <div className="mb-1 flex items-start justify-between gap-2">
        <h3 className="font-semibold">{titel}</h3>
        {felter && admin && !redigerer && (
          <button onClick={() => setRedigerer(true)} className="rounded border border-slate-300 bg-white px-3 py-0.5 text-xs text-slate-700 hover:bg-slate-50">Rediger</button>
        )}
      </div>
      {redigerer && felter ? <Formular felter={felter} d={d} kundeId={kundeId} luk={() => setRedigerer(false)} gemt={gemt} /> : children}
    </div>
  );
}

function Formular({ felter, d, kundeId, luk, gemt }: { felter: Felt[]; d: Data; kundeId: number; luk: () => void; gemt: (d: Data) => void }) {
  const start = Object.fromEntries(felter.map((f) => {
    const v = d[f.felt];
    return [f.felt, Array.isArray(v) ? v.join(", ") : v === null || v === undefined ? "" : String(v)];
  }));
  const [vaerdier, setVaerdier] = useState<Record<string, string>>(start);
  const [fejl, setFejl] = useState<string | null>(null);

  async function gem() {
    const data: Record<string, unknown> = {};
    for (const f of felter) {
      const v = vaerdier[f.felt].trim();
      if (v === start[f.felt]) continue;
      data[f.felt] = f.type === "grupper" ? v.split(/[\s,;]+/).filter(Boolean).map(Number)
        : v === "" ? null : f.type === "tal" || f.felt === "kompensation_paa_rykker" ? Number(v)
        : f.type === "beloeb" ? v.replace(",", ".") : v;
    }
    if (Object.keys(data).length === 0) return luk();
    try {
      gemt(await send<Data>(`/api/opkraevning/${kundeId}/indstillinger`, data));
      luk();
    } catch (e) { setFejl(e instanceof Error ? e.message : "Kunne ikke gemme"); }
  }

  return (
    <div className="space-y-2">
      {felter.map((f) => (
        <label key={f.felt} className="block">
          <span className="text-xs">{f.tekst}</span>
          {f.type === "valg" ? (
            <select value={vaerdier[f.felt]} onChange={(e) => setVaerdier({ ...vaerdier, [f.felt]: e.target.value })}
                    className="mt-0.5 block w-full rounded border border-slate-300 bg-white px-2 py-1 text-slate-900">
              {f.valg!.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
            </select>
          ) : (
            <input value={vaerdier[f.felt]} onChange={(e) => setVaerdier({ ...vaerdier, [f.felt]: e.target.value })}
                   inputMode={f.type === "tekst" ? undefined : "decimal"}
                   className="mt-0.5 block w-full rounded border border-slate-300 bg-white px-2 py-1 text-slate-900" />
          )}
        </label>
      ))}
      {fejl && <p className="text-red-700">{fejl}</p>}
      <div className="flex gap-2 pt-1">
        <button onClick={gem} className="rounded bg-slate-900 px-3 py-1 text-white hover:bg-slate-700">Gem</button>
        <button onClick={luk} className="rounded border border-slate-300 bg-white px-3 py-1 text-slate-700">Annullér</button>
      </div>
      <p className="text-xs opacity-75">Ændringen logges med dit navn.</p>
    </div>
  );
}
