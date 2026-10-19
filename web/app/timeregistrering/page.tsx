"use client";

import { useSearchParams } from "next/navigation";
import { Fragment, Suspense, useCallback, useEffect, useState } from "react";
import { hent, send } from "@/lib/api";
import { dato } from "@/lib/format";

// Ugegitteret: syv dage, mandag–søndag. Al logik (afregningsform, kontrol af timer, summer) ligger på serveren –
// siden viser bare, hvad serveren svarer, og sender linjen, når man trykker Gem.
// Kan åbnes direkte fra en opgave eller en kunde: /timeregistrering/?dato=&kunde=&opgavetype=

type Opgavetype = { kode: string; navn: string; art: "kunde" | "intern" | "fravaer"; nul_timer: boolean };
type KundeValg = { id: number; navn: string; kundenummer: string };
type Linje = {
  opgavetype: string; opgavetype_navn: string; art: string; kunde_id: number | null; kunde: string | null;
  beskrivelse: string | null; saerskilt: boolean; afregning: string[]; timer: Record<string, string>;
  ids: number[]; korrektion: boolean; i_alt: string;
};
type Uge = {
  aar: number; uge: number; dage: string[]; status: string; kan_rettes: boolean; afvisning: string | null;
  linjer: Linje[]; i_alt: { dage: Record<string, string>; uge: string };
  opgavetyper: Opgavetype[]; kunder: KundeValg[];
};
// En linje, som den står i formularen (kan være ændret og ikke gemt endnu).
type Kladde = {
  noegle: string; ids: number[]; opgavetype: string; kunde_id: number | null; beskrivelse: string;
  saerskilt: boolean; timer: Record<string, string>; afregning: string[]; i_alt: string; aendret: boolean;
  fejl: string | null; korrektion: boolean;
};

const DAG = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"];
const STATUS: Record<string, string> = { kladde: "Kladde", afleveret: "Afleveret", afvist: "Afvist", godkendt: "Godkendt" };
const AFREGNING: Record<string, string> = { abonnement: "Abonnement", timeafregning: "Timeafregning" };
const ART: Record<string, string> = { kunde: "Kundearbejde", intern: "Intern tid", fravaer: "Fravær" };

function iDag(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function flyt(iso: string, dage: number): string {
  const [a, m, d] = iso.split("-").map(Number);
  const ny = new Date(Date.UTC(a, m - 1, d + dage));
  return ny.toISOString().slice(0, 10);
}

function timerTekst(v: string | undefined): string {
  if (!v) return "";
  const n = Number(v);
  return n === 0 ? "0" : n.toLocaleString("da-DK", { maximumFractionDigits: 2 });
}

function tilKladde(l: Linje, i: number): Kladde {
  const timer: Record<string, string> = {};
  for (const [d, v] of Object.entries(l.timer)) timer[d] = timerTekst(v);
  return { noegle: `gemt-${l.ids[0] ?? i}`, ids: l.ids, opgavetype: l.opgavetype, kunde_id: l.kunde_id,
           beskrivelse: l.beskrivelse ?? "", saerskilt: l.saerskilt, timer, afregning: l.afregning, i_alt: l.i_alt,
           aendret: false, fejl: null, korrektion: l.korrektion };
}

let taeller = 0;
function nyKladde(opgavetype = "", kunde_id: number | null = null): Kladde {
  taeller += 1;
  return { noegle: `ny-${taeller}`, ids: [], opgavetype, kunde_id, beskrivelse: "", saerskilt: false, timer: {},
           afregning: [], i_alt: "0", aendret: false, fejl: null, korrektion: false };
}

export default function Side() {
  return (
    <Suspense fallback={<p className="text-slate-500">Henter …</p>}>
      <Ugegitter />
    </Suspense>
  );
}

function Ugegitter() {
  const params = useSearchParams();
  const [dag, setDag] = useState(() => params.get("dato") || iDag());
  const [uge, setUge] = useState<Uge | null>(null);
  const [linjer, setLinjer] = useState<Kladde[]>([]);
  const [fejl, setFejl] = useState<string | null>(null);
  const [startet, setStartet] = useState(false);

  const vis = useCallback((u: Uge, ekstra: Kladde[] = []) => {
    setUge(u);
    setLinjer([...u.linjer.map(tilKladde), ...ekstra]);
  }, []);

  useEffect(() => {
    setFejl(null);
    hent<Uge>(`/api/tid/uge?dato=${dag}`)
      .then((u) => {
        // Åbnet fra en opgave/kunde: start med en ny linje udfyldt med kunden og opgavetypen.
        const kunde = params.get("kunde");
        const ekstra = !startet && (kunde || params.get("opgavetype"))
          ? [nyKladde(params.get("opgavetype") ?? "", kunde ? Number(kunde) : null)] : [];
        setStartet(true);
        vis(u, ekstra);
      })
      .catch((e) => setFejl(e instanceof Error ? e.message : "Ugen kunne ikke hentes"));
  }, [dag]); // eslint-disable-line react-hooks/exhaustive-deps

  function ret(noegle: string, aendring: Partial<Kladde>) {
    setLinjer((ls) => ls.map((l) => (l.noegle === noegle ? { ...l, ...aendring, aendret: true, fejl: null } : l)));
  }

  async function vaelgKunde(l: Kladde, kunde_id: number | null) {
    ret(l.noegle, { kunde_id, afregning: [] });
    if (kunde_id && uge) {
      const svar = await hent<{ afregning: string | null }>(`/api/tid/afregning?kunde=${kunde_id}&dato=${uge.dage[0]}`);
      setLinjer((ls) => ls.map((x) => (x.noegle === l.noegle ? { ...x, afregning: svar.afregning ? [svar.afregning] : ["mangler"] } : x)));
    }
  }

  async function gem(l: Kladde) {
    if (!uge) return;
    const andre = linjer.filter((x) => x.noegle !== l.noegle && x.aendret);
    try {
      const u = await send<Uge>("/api/tid/linje", {
        dato: uge.dage[0], ids: l.ids, opgavetype: l.opgavetype, kunde_id: l.kunde_id,
        beskrivelse: l.beskrivelse || null, saerskilt: l.saerskilt, timer: l.timer,
      });
      vis(u, andre.filter((x) => x.ids.length === 0));
    } catch (e) {
      setLinjer((ls) => ls.map((x) => (x.noegle === l.noegle ? { ...x, fejl: e instanceof Error ? e.message : "Linjen kunne ikke gemmes" } : x)));
    }
  }

  async function slet(l: Kladde) {
    if (!uge) return;
    if (l.ids.length === 0) { setLinjer((ls) => ls.filter((x) => x.noegle !== l.noegle)); return; }
    if (!window.confirm("Slet linjen med alle dens timer i ugen?")) return;
    try {
      vis(await send<Uge>("/api/tid/linje/slet", { dato: uge.dage[0], ids: l.ids }),
          linjer.filter((x) => x.ids.length === 0 && x.noegle !== l.noegle));
    } catch (e) {
      setLinjer((ls) => ls.map((x) => (x.noegle === l.noegle ? { ...x, fejl: e instanceof Error ? e.message : "Linjen kunne ikke slettes" } : x)));
    }
  }

  if (fejl) return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold">Timeregistrering</h1>
      <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-amber-900">{fejl}</p>
    </div>
  );
  if (!uge) return <p className="text-slate-500">Henter …</p>;

  const typer = (art: string) => uge.opgavetyper.filter((t) => t.art === art);
  const felt = "w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm disabled:bg-slate-50 disabled:text-slate-500";
  const ulaast = uge.kan_rettes;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Timeregistrering</h1>
          <p className="text-sm text-slate-600">Timer skrives som decimaltal i kvarte timer: 1,25 = 1 time og 15 minutter.</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <button type="button" onClick={() => setDag(flyt(uge.dage[0], -7))} aria-label="Forrige uge"
                  className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50">‹</button>
          <span className="min-w-56 text-center font-medium">
            Uge {uge.uge} · {dato(uge.dage[0])} – {dato(uge.dage[6])}
          </span>
          <button type="button" onClick={() => setDag(flyt(uge.dage[0], 7))} aria-label="Næste uge"
                  className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50">›</button>
          <button type="button" onClick={() => setDag(iDag())} className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50">I dag</button>
          <span className={`ml-2 rounded px-2 py-0.5 text-xs font-semibold ${uge.status === "godkendt" ? "bg-emerald-100 text-emerald-800"
            : uge.status === "afvist" ? "bg-red-100 text-red-800" : uge.status === "afleveret" ? "bg-sky-100 text-sky-800" : "bg-slate-200 text-slate-700"}`}>
            {STATUS[uge.status] ?? uge.status}
          </span>
        </div>
      </div>

      {uge.afvisning && <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">Ugen er afvist: {uge.afvisning}</p>}
      {!ulaast && <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
        Ugen er {STATUS[uge.status]?.toLowerCase()} og kan ikke rettes.</p>}

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
            <tr>
              <th className="px-2 py-2">Opgavetype</th>
              <th className="px-2 py-2">Kunde</th>
              <th className="px-2 py-2">Beskrivelse</th>
              <th className="px-2 py-2" title="Abonnementskunder: er arbejdet indeholdt i abonnementet, eller afregnes det som timer?">Afregning</th>
              {uge.dage.map((d, i) => (
                <th key={d} className={`px-1 py-2 text-center ${i >= 5 ? "text-slate-400" : ""}`}>{DAG[i]}<br />{dato(d).slice(0, 5)}</th>
              ))}
              <th className="whitespace-nowrap px-2 py-2 text-right">I alt</th>
              <th className="px-2 py-2" />
            </tr>
          </thead>
          <tbody>
            {linjer.map((l) => {
              const t = uge.opgavetyper.find((x) => x.kode === l.opgavetype);
              const kundetid = t?.art === "kunde";
              return (
                <Fragment key={l.noegle}>
                <tr className={`align-top ${l.fejl ? "" : "border-b border-slate-100"}`}>
                  <td className="w-48 px-2 py-2">
                    <select value={l.opgavetype} disabled={!ulaast} aria-label="Opgavetype"
                            onChange={(e) => {
                              const ny = uge.opgavetyper.find((x) => x.kode === e.target.value);
                              const timer = ny?.nul_timer ? {} : l.timer;  // dage med "ikke på arbejde" markeres – ingen timer
                              ret(l.noegle, ny?.art === "kunde" ? { opgavetype: e.target.value, timer } : { opgavetype: e.target.value, kunde_id: null, saerskilt: false, afregning: [], timer });
                            }} className={`${felt} min-w-40`}>
                      <option value="">Vælg …</option>
                      {(["kunde", "intern", "fravaer"] as const).map((art) => (
                        <optgroup key={art} label={ART[art]}>
                          {typer(art).map((x) => <option key={x.kode} value={x.kode}>{x.navn}</option>)}
                        </optgroup>
                      ))}
                    </select>
                    {l.korrektion && <span className="mt-1 inline-block rounded bg-amber-100 px-1.5 text-xs text-amber-800">Korrektion</span>}
                  </td>
                  <td className="w-56 px-2 py-2">
                    <select value={l.kunde_id ?? ""} disabled={!ulaast || !kundetid} aria-label="Kunde"
                            onChange={(e) => vaelgKunde(l, e.target.value ? Number(e.target.value) : null)}
                            className={`${felt} min-w-44 ${kundetid && !l.kunde_id && ulaast ? "border-amber-400" : ""}`}>
                      <option value="">{kundetid ? "Vælg kunde (skal udfyldes)" : "—"}</option>
                      {uge.kunder.map((k) => <option key={k.id} value={k.id}>{k.navn}</option>)}
                    </select>
                    {kundetid && l.afregning.includes("mangler") && (
                      <div className="mt-1 text-xs text-red-700">Mangler afregningsform – en administrator skal sætte den</div>
                    )}
                  </td>
                  <td className="px-2 py-2">
                    <input value={l.beskrivelse} disabled={!ulaast} aria-label="Beskrivelse"
                           placeholder={l.saerskilt ? "Skal udfyldes – står på fakturaen" : ""}
                           onChange={(e) => ret(l.noegle, { beskrivelse: e.target.value })}
                           className={`${felt} min-w-40 ${l.saerskilt && !l.beskrivelse.trim() && ulaast ? "border-amber-400" : ""}`} />
                  </td>
                  <td className="px-2 py-2">
                    {!kundetid || l.afregning.length === 0 || l.afregning.includes("mangler") ? (
                      <span className="text-slate-400">—</span>
                    ) : l.afregning.includes("abonnement") ? (
                      // Abonnementskunde: medarbejderen vælger selv, om arbejdet er indeholdt (bygherren 19.10.2026).
                      <select value={l.saerskilt ? "uden_for" : "indeholdt"} disabled={!ulaast} aria-label="Afregning"
                              onChange={(e) => ret(l.noegle, { saerskilt: e.target.value === "uden_for" })} title={l.saerskilt ? "Timeafregning – arbejdet er uden for abonnementet og faktureres pr. time" : "Arbejdet er indeholdt i abonnementet"}
                              className={`${felt} min-w-52`}>
                        <option value="indeholdt">Indeholdt i abonnementet</option>
                        <option value="uden_for">Uden for abonnementet – timer</option>
                      </select>
                    ) : (
                      <span className="text-slate-600">{l.afregning.map((a) => AFREGNING[a] ?? a).join(" / ")}</span>
                    )}
                  </td>
                  {uge.dage.map((d, i) => (
                    <td key={d} className={`px-1 py-2 ${i >= 5 ? "bg-slate-50" : ""}`}>
                      {t?.nul_timer ? (
                        // Fx "Ikke på arbejde": altid 0 timer – dagen markeres i stedet for at få timer.
                        <label className="flex w-12 justify-center py-1">
                          <input type="checkbox" checked={l.timer[d] !== undefined && l.timer[d] !== ""} disabled={!ulaast}
                                 aria-label={`Markér ${DAG[i]} ${dato(d)}`}
                                 onChange={(e) => ret(l.noegle, { timer: { ...l.timer, [d]: e.target.checked ? "0" : "" } })} />
                        </label>
                      ) : (
                        <input value={l.timer[d] ?? ""} disabled={!ulaast} inputMode="decimal"
                               aria-label={`Timer ${DAG[i]} ${dato(d)}`}
                               onChange={(e) => ret(l.noegle, { timer: { ...l.timer, [d]: e.target.value } })}
                               className="w-12 rounded border border-slate-300 px-1 py-1 text-right tabular-nums disabled:bg-slate-50" />
                      )}
                    </td>
                  ))}
                  <td className="px-2 py-2 text-right tabular-nums">{l.aendret ? <span className="text-slate-400">–</span> : timerTekst(l.i_alt) || "0"}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-right">
                    {ulaast && l.aendret && (
                      <button type="button" onClick={() => gem(l)} className="rounded bg-brand-600 px-3 py-1 text-xs text-white hover:bg-brand-700">Gem</button>
                    )}
                    {ulaast && (
                      <button type="button" onClick={() => slet(l)} aria-label="Slet linjen"
                              className="ml-2 text-xs text-slate-500 hover:text-red-700">Slet</button>
                    )}
                  </td>
                </tr>
                {l.fejl && (
                  <tr className="border-b border-slate-100">
                    <td colSpan={13} className="px-2 pb-2">
                      <p role="alert" className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-800">{l.fejl}</p>
                    </td>
                  </tr>
                )}
                </Fragment>
              );
            })}
            {linjer.length === 0 && (
              <tr><td colSpan={13} className="px-3 py-4 text-center text-slate-500">Ingen timer i ugen endnu.</td></tr>
            )}
          </tbody>
          <tfoot className="border-t border-slate-200 text-xs font-semibold text-slate-700">
            <tr>
              <td colSpan={4} className="px-2 py-2">
                {ulaast && (
                  <button type="button" onClick={() => setLinjer((ls) => [...ls, nyKladde()])}
                          className="rounded border border-slate-300 px-3 py-1 font-normal hover:bg-slate-50">+ Tilføj linje</button>
                )}
                <span className="ml-3 font-normal text-slate-500">Summerne gælder de gemte linjer.</span>
              </td>
              {uge.dage.map((d, i) => (
                <td key={d} className={`px-1 py-2 text-right tabular-nums ${i >= 5 ? "bg-slate-50" : ""}`}>{timerTekst(uge.i_alt.dage[d]) || "0"}</td>
              ))}
              <td className="px-2 py-2 text-right tabular-nums">{timerTekst(uge.i_alt.uge) || "0"}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
