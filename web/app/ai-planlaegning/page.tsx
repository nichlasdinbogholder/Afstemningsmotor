"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useMig } from "@/components/Ramme";
import AdminLaas from "@/components/AdminLaas";
import { hent, send } from "@/lib/api";
import { type Valg } from "@/lib/kartotek";

// AI Planlægning (bygherren 26.10.2026): kommende frister for moms, lønsumsafgift og årsafslutning. Kundens ordning vælges
// på kundekortets fane Frister. Moms/lønsumsafgift: Skats fristkalender (godkendt af admin, beregnes aldrig).
// Årsafslutning: regnet på serveren ud fra kundens regnskabsår efter den godkendte regeltabel.
type Raekke = {
  kunde_id: number; kunde: string; kundenummer: string; ansvarlig: string | null; assistent: string | null;
  afgift: string; afgift_tekst: string; ordning_tekst: string; periode: string; frist: string; dage_til: number;
};
type Svar = { raekker: Raekke[]; advarsler: string[]; fra: string; til: string };
type Frist = {
  id: number; afgift: string; afgift_tekst: string; ordning: string; ordning_tekst: string; periode: string;
  periode_fra: string; periode_til: string; frist: string; kilde: string; status: string;
};

const dato = (iso: string) => new Date(iso).toLocaleDateString("da-DK", { weekday: "short", day: "numeric", month: "short", year: "numeric" });

export default function Side() {
  const mig = useMig();
  const admin = mig?.rolle === "admin";
  const [fane, setFane] = useState<"frister" | "kalender">("frister");
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">AI Planlægning</h1>
      <nav className="flex gap-1 border-b border-slate-200 text-sm">
        {([["frister", "Kommende frister"], ["kalender", "Skats fristkalender"]] as const).map(([k, tekst]) => (
          <button key={k} onClick={() => setFane(k)}
                  className={`-mb-px border-b-2 px-3 py-2 ${fane === k ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900"}`}>
            {tekst}
          </button>
        ))}
      </nav>
      {fane === "frister" ? <KommendeFrister /> : <Fristkalender admin={admin} />}
    </div>
  );
}

function KommendeFrister() {
  const [valg, setValg] = useState<Valg | null>(null);
  const [filtre, setFiltre] = useState({ mine: true, ansvarlig_id: "", afgift: "", maaneder: "3" });
  const [svar, setSvar] = useState<Svar | null>(null);
  useEffect(() => { hent<Valg>("/api/kundekartotek/valg").then(setValg).catch(() => {}); }, []);
  useEffect(() => {
    const fra = new Date();
    const til = new Date(fra);
    til.setMonth(til.getMonth() + Number(filtre.maaneder));
    const q = new URLSearchParams({ fra: fra.toLocaleDateString("sv-SE"), til: til.toLocaleDateString("sv-SE") });
    if (filtre.mine) q.set("mine", "true");
    if (filtre.ansvarlig_id) q.set("ansvarlig_id", filtre.ansvarlig_id);
    if (filtre.afgift) q.set("afgift", filtre.afgift);
    hent<Svar>(`/api/planlaegning/frister?${q}`).then(setSvar);
  }, [filtre]);

  const felt = "rounded-md border border-slate-300 px-2 py-1.5";
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <label className="flex flex-col gap-1"><span className="text-xs font-medium text-slate-500">Frist</span>
          <select value={filtre.afgift} onChange={(e) => setFiltre({ ...filtre, afgift: e.target.value })} className={felt}>
            <option value="">Alle frister</option><option value="moms">Moms</option><option value="loensumsafgift">Lønsumsafgift</option>
            <option value="aarsafslutning">Årsafslutning</option>
          </select>
        </label>
        <label className="flex flex-col gap-1"><span className="text-xs font-medium text-slate-500">Ansvarlig</span>
          <select value={filtre.ansvarlig_id} onChange={(e) => setFiltre({ ...filtre, ansvarlig_id: e.target.value })} className={felt}>
            <option value="">Alle</option>
            {valg?.medarbejdere.map((m) => <option key={m.id} value={m.id}>{m.navn}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1"><span className="text-xs font-medium text-slate-500">Periode</span>
          <select value={filtre.maaneder} onChange={(e) => setFiltre({ ...filtre, maaneder: e.target.value })} className={felt}>
            <option value="1">Næste måned</option><option value="3">Næste 3 måneder</option>
            <option value="6">Næste 6 måneder</option><option value="12">Næste 12 måneder</option>
          </select>
        </label>
        <button type="button" aria-pressed={filtre.mine} onClick={() => setFiltre({ ...filtre, mine: !filtre.mine })}
                className={`rounded-full border px-3 py-1.5 ${filtre.mine ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
          {filtre.mine ? "✓ " : ""}Mine kunder
        </button>
        {svar && <span className="ml-auto text-slate-500">{svar.raekker.length} frister</span>}
      </div>
      {svar?.advarsler.map((a) => <p key={a} className="rounded-md bg-amber-50 px-3 py-2 text-amber-900">{a}</p>)}
      {!svar ? <p className="text-slate-500">Henter …</p> : svar.raekker.length === 0 ? (
        <p className="text-slate-500">Ingen frister i perioden.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr><th className="px-3 py-2">Frist</th><th className="px-3 py-2">Kunde</th><th className="px-3 py-2">Hvad</th>
                <th className="px-3 py-2">Periode</th><th className="px-3 py-2">Ansvarlig</th></tr>
            </thead>
            <tbody>
              {svar.raekker.map((r) => (
                <tr key={`${r.kunde_id}-${r.afgift}-${r.frist}`} className="border-b border-slate-100">
                  <td className="px-3 py-2">
                    <span className="font-medium">{dato(r.frist)}</span>
                    <span className={`ml-2 text-xs ${r.dage_til <= 7 ? "font-semibold text-red-700" : r.dage_til <= 30 ? "text-amber-700" : "text-slate-500"}`}>
                      {r.dage_til === 0 ? "i dag" : `om ${r.dage_til} dage`}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <Link href={`/kunder/kort/?id=${r.kunde_id}&fane=frister`} className="font-medium text-slate-900 hover:text-brand-700 hover:underline">{r.kunde}</Link>
                    <span className="ml-1 text-xs text-slate-500">{r.kundenummer}</span>
                  </td>
                  <td className="px-3 py-2">{r.afgift_tekst} <span className="text-xs text-slate-500">· {r.ordning_tekst}</span></td>
                  <td className="px-3 py-2">{r.periode}</td>
                  <td className="px-3 py-2">{r.ansvarlig ?? <span className="text-slate-400">Ingen</span>}
                    {r.assistent && <div className="text-xs text-slate-500">Assistent: {r.assistent}</div>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const ORDNINGER: Record<string, [string, string][]> = {
  moms: [["maaned", "Månedlig"], ["kvartal", "Kvartalsvis"], ["halvaar", "Halvårlig"]],
  loensumsafgift: [["metode_1", "Metode 1"], ["metode_2", "Metode 2"], ["metode_3", "Metode 3"], ["metode_4", "Metode 4"]],
};

// Skats fristkalender: datoerne, som Skat har offentliggjort. Alle ser; KUN administratorer lægger ind og tilbagekalder.
function Fristkalender({ admin }: { admin: boolean }) {
  const [liste, setListe] = useState<Frist[] | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const [ny, setNy] = useState({ afgift: "moms", ordning: "kvartal", periode_fra: "", periode_til: "", frist: "", kilde: "" });
  const hentListe = useCallback(() => { hent<Frist[]>("/api/planlaegning/kalender").then(setListe); }, []);
  useEffect(() => { hentListe(); }, [hentListe]);

  async function udfoer(handling: () => Promise<unknown>, ok: string): Promise<boolean> {
    try { await handling(); setBesked({ tekst: ok }); hentListe(); return true; }
    catch (e) { setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true }); return false; }
  }
  const felt = "mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5";
  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-600">
        Fristerne beregnes ikke – de er de datoer, Skat har offentliggjort (se skat.dk under moms og lønsumsafgift).
        Kun godkendte frister bruges i listen over kommende frister.
      </p>
      {besked && <p className={`rounded-md px-3 py-2 ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}
      {admin && (
        <form onSubmit={async (e) => { e.preventDefault(); if (await udfoer(() => send("/api/planlaegning/kalender", ny), "Fristen er lagt ind")) setNy({ ...ny, periode_fra: "", periode_til: "", frist: "" }); }}
              className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="mb-3 font-medium">Læg en frist ind fra Skats fristkalender <AdminLaas /></p>
          <div className="grid grid-cols-6 gap-3">
            <label>Afgift
              <select value={ny.afgift} onChange={(e) => setNy({ ...ny, afgift: e.target.value, ordning: ORDNINGER[e.target.value][0][0] })} className={felt}>
                <option value="moms">Moms</option><option value="loensumsafgift">Lønsumsafgift</option>
              </select>
            </label>
            <label>Ordning
              <select value={ny.ordning} onChange={(e) => setNy({ ...ny, ordning: e.target.value })} className={felt}>
                {ORDNINGER[ny.afgift].map(([k, t]) => <option key={k} value={k}>{t}</option>)}
              </select>
            </label>
            <label>Periode fra<input type="date" required value={ny.periode_fra} onChange={(e) => setNy({ ...ny, periode_fra: e.target.value })} className={felt} /></label>
            <label>Periode til<input type="date" required value={ny.periode_til} onChange={(e) => setNy({ ...ny, periode_til: e.target.value })} className={felt} /></label>
            <label>Frist<input type="date" required value={ny.frist} onChange={(e) => setNy({ ...ny, frist: e.target.value })} className={felt} /></label>
            <label>Kilde (side på skat.dk)<input type="url" required value={ny.kilde} placeholder="https://skat.dk/…" onChange={(e) => setNy({ ...ny, kilde: e.target.value })} className={felt} /></label>
          </div>
          <button type="submit" className="mt-3 rounded-md bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-700">Læg ind</button>
        </form>
      )}
      {!liste ? <p className="text-slate-500">Henter …</p> : liste.length === 0 ? (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-amber-900">Der er ikke lagt nogen frister ind endnu.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr><th className="px-3 py-2">Afgift</th><th className="px-3 py-2">Ordning</th><th className="px-3 py-2">Periode</th>
                <th className="px-3 py-2">Frist</th><th className="px-3 py-2">Status</th><th className="px-3 py-2" /></tr>
            </thead>
            <tbody>
              {liste.map((f) => (
                <tr key={f.id} className="border-b border-slate-100">
                  <td className="px-3 py-2">{f.afgift_tekst}</td><td className="px-3 py-2">{f.ordning_tekst}</td>
                  <td className="px-3 py-2">{f.periode}</td>
                  <td className="px-3 py-2"><a href={f.kilde} target="_blank" rel="noreferrer" className="font-medium hover:underline">{dato(f.frist)}</a></td>
                  <td className="px-3 py-2">{f.status === "udkast" ? <span className="text-amber-700">Udkast – venter på godkendelse</span> : "Godkendt"}</td>
                  <td className="px-3 py-2 text-right">
                    {admin && f.status === "udkast" && (
                      <button onClick={() => udfoer(() => send(`/api/planlaegning/kalender/${f.id}/godkend`), "Fristen er godkendt")}
                              className="mr-2 rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50"><AdminLaas className="mr-1" />Godkend</button>
                    )}
                    {admin && (
                      <button onClick={() => window.confirm("Tilbagekald fristen? Den bruges så ikke længere.") &&
                                        udfoer(() => send(`/api/planlaegning/kalender/${f.id}/tilbagekald`), "Fristen er tilbagekaldt")}
                              className="rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50"><AdminLaas className="mr-1" />Tilbagekald</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
