"use client";

import { useCallback, useEffect, useState } from "react";
import { useMig } from "@/components/Ramme";
import { hent, send } from "@/lib/api";
import { dato, tidspunkt } from "@/lib/format";
import Svartekst from "./Svartekst";

// Svends videnbase: alle kan læse, kun administratorer kan oprette og rette. Dokumenter slettes aldrig –
// sæt en slutdato ("gyldig til") eller gør dem til kladde. Reglerne kontrolleres på serveren.

type Dok = {
  id: number; titel: string; kategori: string; status: string; vaerdi: string | null; enhed: string | null;
  kilde: string | null; gyldig_fra: string | null; gyldig_til: string | null; senest_bekraeftet: string | null;
  kontrol_kilde: string | null; kontrol_hint: string | null; rettet_af: string | null; rettet: string; tekst?: string;
};

const KATEGORIER: Record<string, string> = {
  moms: "Moms", skat: "Skat", bogfoering: "Bogføring", intern: "Intern", kontoplan: "Kontoplan", satser: "Satser",
};
const STATUS: Record<string, { tekst: string; farve: string }> = {
  active: { tekst: "Aktiv", farve: "bg-emerald-100 text-emerald-800" },
  draft: { tekst: "Kladde", farve: "bg-amber-100 text-amber-900" },
  superseded: { tekst: "Afløst", farve: "bg-slate-200 text-slate-600" },
};
const TOM = { titel: "", kategori: "intern", tekst: "", status: "active", kilde: "", gyldig_fra: "", gyldig_til: "",
              vaerdi: "", enhed: "", kontrol_kilde: "", kontrol_hint: "" };
type Formular = typeof TOM;

function formularFra(d: Dok): Formular {
  return { titel: d.titel, kategori: d.kategori, tekst: d.tekst ?? "", status: d.status, kilde: d.kilde ?? "",
           gyldig_fra: d.gyldig_fra ?? "", gyldig_til: d.gyldig_til ?? "", vaerdi: d.vaerdi ? vaerdi(d.vaerdi).replace(/\./g, "") : "", enhed: d.enhed ?? "",
           kontrol_kilde: d.kontrol_kilde ?? "", kontrol_hint: d.kontrol_hint ?? "" };
}

function Felt({ navn, children, hjaelp }: { navn: string; children: React.ReactNode; hjaelp?: string }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{navn}</span>
      <div className="mt-1">{children}</div>
      {hjaelp && <span className="mt-1 block text-xs text-slate-500">{hjaelp}</span>}
    </label>
  );
}

// Satsens værdi på dansk uden overflødige nuller: "1.5000" → "1,5".
function vaerdi(v: string): string {
  return Number(v).toLocaleString("da-DK", { maximumFractionDigits: 4 });
}

function Besked({ tekst, fejl }: { tekst: string; fejl?: boolean }) {
  return (
    <p role={fejl ? "alert" : "status"}
       className={`mb-3 rounded-md px-3 py-2 text-sm ${fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>
      {tekst}
    </p>
  );
}

const INPUT = "w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-brand-600 focus:outline-none";

export default function Videnbase({ start = null }: { start?: number | null }) {
  const mig = useMig();
  const admin = mig?.rolle === "admin";
  const [kategori, setKategori] = useState("");
  const [liste, setListe] = useState<Dok[] | null>(null);
  const [valgt, setValgt] = useState<Dok | null>(null);
  const [formular, setFormular] = useState<Formular | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);

  const hentListe = useCallback(() => {
    hent<Dok[]>(`/api/v1/kb/documents${kategori ? `?kategori=${kategori}` : ""}`).then(setListe);
  }, [kategori]);
  useEffect(() => { hentListe(); }, [hentListe]);
  // Åbn et bestemt dokument, når siden er åbnet fra en kilde under et svar.
  useEffect(() => {
    if (start !== null) hent<Dok>(`/api/v1/kb/documents/${start}`).then(setValgt).catch(() => {});
  }, [start]);

  async function aabn(id: number) {
    const d = await hent<Dok>(`/api/v1/kb/documents/${id}`);
    setValgt(d);
    setFormular(null);
    setBesked(null);
  }

  async function gem() {
    if (!formular) return;
    // "1.5" kan betyde både 1,5 og 15 – en sats må ikke gættes, så punktum uden komma afvises.
    const v = formular.vaerdi.trim();
    if (v && !/^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$/.test(v)) {
      setBesked({ tekst: "Skriv værdien med komma som decimaltegn, fx 3,79 eller 1.234,50", fejl: true });
      return;
    }
    const data = { ...formular, vaerdi: formular.vaerdi.trim() ? formular.vaerdi.trim().replace(/\./g, "").replace(",", ".") : null,
                   gyldig_fra: formular.gyldig_fra || null, gyldig_til: formular.gyldig_til || null };
    try {
      const d = await send<Dok>(valgt ? `/api/v1/kb/documents/${valgt.id}` : "/api/v1/kb/documents", data);
      setValgt(d);
      setFormular(null);
      setBesked({ tekst: "Gemt. Ændringen er logget med dit navn." });
      hentListe();
    } catch (e) {
      setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true });
    }
  }

  const s = (felt: keyof Formular) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setFormular((f) => (f ? { ...f, [felt]: e.target.value } : f));

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <div>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <select value={kategori} onChange={(e) => setKategori(e.target.value)} aria-label="Kategori" className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm">
            <option value="">Alle kategorier</option>
            {Object.entries(KATEGORIER).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
          </select>
          {admin && (
            <button type="button" onClick={() => { setValgt(null); setFormular({ ...TOM }); setBesked(null); }}
                    className="rounded-md bg-brand-600 px-4 py-1.5 text-sm text-white hover:bg-brand-700">Nyt dokument</button>
          )}
        </div>
        {!liste ? <p className="text-slate-500">Henter …</p> : liste.length === 0 ? (
          <p className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
            Videnbasen er tom. {admin ? "Klik ”Nyt dokument” for at lægge jeres eget materiale ind." : "En administrator lægger materialet ind."}
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {liste.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => aabn(d.id)}
                        className={`w-full px-4 py-2.5 text-left text-sm hover:bg-slate-50 ${valgt?.id === d.id ? "bg-slate-50" : ""}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-slate-900">{d.titel}</span>
                    <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${STATUS[d.status]?.farve}`}>{STATUS[d.status]?.tekst}</span>
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">
                    {KATEGORIER[d.kategori]}
                    {d.vaerdi && <> · {vaerdi(d.vaerdi)} {d.enhed}</>}
                    {d.gyldig_til && <> · gyldig til {dato(d.gyldig_til)}</>}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        {besked && !formular && <Besked {...besked} />}
        {formular ? (
          <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-5">
            <p className="font-semibold">{valgt ? "Ret dokument" : "Nyt dokument"}</p>
            <Felt navn="Titel"><input value={formular.titel} onChange={s("titel")} className={INPUT} /></Felt>
            <div className="grid gap-4 sm:grid-cols-2">
              <Felt navn="Kategori">
                <select value={formular.kategori} onChange={s("kategori")} className={INPUT}>
                  {Object.entries(KATEGORIER).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
                </select>
              </Felt>
              <Felt navn="Status" hjaelp="Kun aktive dokumenter bruges af Svend.">
                <select value={formular.status} onChange={s("status")} className={INPUT}>
                  <option value="active">Aktiv</option><option value="draft">Kladde</option>
                </select>
              </Felt>
            </div>
            <Felt navn="Tekst" hjaelp="Skriv som til en kollega. **fed** og punkter med “- ” vises pænt.">
              <textarea rows={10} value={formular.tekst} onChange={s("tekst")} className={INPUT} />
            </Felt>
            <Felt navn="Kildelink (valgfrit)"><input value={formular.kilde} onChange={s("kilde")} placeholder="https://skat.dk/…" className={INPUT} /></Felt>
            <div className="grid gap-4 sm:grid-cols-2">
              <Felt navn="Gyldig fra (valgfrit)"><input type="date" value={formular.gyldig_fra} onChange={s("gyldig_fra")} className={INPUT} /></Felt>
              <Felt navn="Gyldig til (valgfrit)" hjaelp="Efter denne dato bruger Svend det ikke."><input type="date" value={formular.gyldig_til} onChange={s("gyldig_til")} className={INPUT} /></Felt>
            </div>
            {formular.kategori === "satser" && (
              <div className="space-y-4 rounded-md border border-slate-200 bg-slate-50 p-4">
                <p className="text-sm font-medium text-slate-700">Satsen</p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Felt navn="Værdi"><input value={formular.vaerdi} onChange={s("vaerdi")} inputMode="decimal" className={INPUT} /></Felt>
                  <Felt navn="Enhed">
                    <input value={formular.enhed} onChange={s("enhed")} list="enheder" placeholder="kr/km, pct, kr" className={INPUT} />
                    <datalist id="enheder"><option value="kr/km" /><option value="pct" /><option value="kr" /></datalist>
                  </Felt>
                </div>
                <Felt navn="Side, hvor satsen kontrolleres (valgfrit)" hjaelp="Bruges af den ugentlige satskontrol (fase 3).">
                  <input value={formular.kontrol_kilde} onChange={s("kontrol_kilde")} placeholder="https://…" className={INPUT} />
                </Felt>
                <Felt navn="Sådan findes værdien på siden (valgfrit)">
                  <input value={formular.kontrol_hint} onChange={s("kontrol_hint")} placeholder="Fx: tallet i rækken 'over 20.000 km'" className={INPUT} />
                </Felt>
                <p className="text-xs text-slate-500">Når du gemmer en ny værdi, regnes satsen som bekræftet af dig i dag.</p>
              </div>
            )}
            {besked && <Besked {...besked} />}
            <div className="flex gap-2">
              <button type="button" onClick={gem} className="rounded-md bg-brand-600 px-4 py-1.5 text-sm text-white hover:bg-brand-700">Gem</button>
              <button type="button" onClick={() => setFormular(null)} className="rounded-md border border-slate-300 px-4 py-1.5 text-sm hover:bg-slate-50">Annullér</button>
            </div>
          </div>
        ) : valgt ? (
          <div className="rounded-lg border border-slate-200 bg-white p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-lg font-semibold">{valgt.titel}</p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {KATEGORIER[valgt.kategori]} · {STATUS[valgt.status]?.tekst}
                  {(valgt.gyldig_fra || valgt.gyldig_til) && <> · gyldig {valgt.gyldig_fra ? `fra ${dato(valgt.gyldig_fra)}` : ""} {valgt.gyldig_til ? `til ${dato(valgt.gyldig_til)}` : ""}</>}
                </p>
              </div>
              {admin && valgt.status !== "superseded" && (
                <button type="button" onClick={() => { setFormular(formularFra(valgt)); setBesked(null); }}
                        className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">Ret</button>
              )}
            </div>
            {valgt.vaerdi && (
              <p className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm">
                <span className="font-semibold">{vaerdi(valgt.vaerdi)} {valgt.enhed}</span>
                <span className="text-slate-500"> · senest bekræftet {valgt.senest_bekraeftet ? tidspunkt(valgt.senest_bekraeftet) : "aldrig"}</span>
              </p>
            )}
            <div className="mt-4 text-sm leading-relaxed text-slate-800"><Svartekst tekst={valgt.tekst ?? ""} /></div>
            {valgt.kilde && <p className="mt-4 text-sm"><a href={valgt.kilde} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline">Kilde ↗</a></p>}
            <p className="mt-4 text-xs text-slate-400">Senest rettet {tidspunkt(valgt.rettet)}{valgt.rettet_af ? ` af ${valgt.rettet_af}` : ""}</p>
          </div>
        ) : (
          <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">Vælg et dokument i listen.</p>
        )}
      </div>
    </div>
  );
}
