"use client";

import { useCallback, useEffect, useState } from "react";
import { hent, send } from "@/lib/api";
import { tidspunkt } from "@/lib/format";

// Satskontrollens fund (kun administratorer): gammel og ny værdi side om side med teksten fra kildesiden.
// INTET bliver aktivt, før en administrator har godkendt det her – databasen afviser det ellers.

type Kontrol = {
  id: number; tidspunkt: string; udfald: "changed" | "failed" | "unchanged" | "stale"; metode: "regel" | "model" | null;
  dokument: { id: number; titel: string; enhed: string | null; kontrol_kilde: string | null; kontrol_hint: string | null;
              gyldig_fra: string | null };
  gammel_vaerdi: string | null; ny_vaerdi: string | null; uddrag: string | null; fejl: string | null;
  afgoerelse: string | null; afgjort_af: string | null; afgjort: string | null; note: string | null;
};

function tal(v: string | null): string {
  return v === null ? "–" : Number(v).toLocaleString("da-DK", { maximumFractionDigits: 4 });
}

const AFGOERELSE: Record<string, string> = { approved: "Godkendt", rejected: "Afvist", noted: "Kvitteret" };

function Kort({ k, opdater }: { k: Kontrol; opdater: () => void }) {
  const [gyldigFra, setGyldigFra] = useState("");
  const [note, setNote] = useState("");
  const [fejl, setFejl] = useState<string | null>(null);

  async function udfoer(sti: string, data: unknown) {
    try {
      setFejl(null);
      await send(sti, data);
      opdater();
    } catch (e) {
      setFejl(e instanceof Error ? e.message : "Det lykkedes ikke");
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-semibold">{k.dokument.titel}</p>
          <p className="text-xs text-slate-500">{k.udfald === "stale" ? "Opdaget" : "Kontrolleret"} {tidspunkt(k.tidspunkt)}</p>
        </div>
        {k.afgoerelse ? (
          <span className="rounded bg-slate-200 px-2 py-0.5 text-xs font-semibold text-slate-700">
            {AFGOERELSE[k.afgoerelse]}{k.afgjort_af ? ` af ${k.afgjort_af}` : ""}
          </span>
        ) : k.udfald === "changed" ? (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">Ny værdi fundet</span>
        ) : k.udfald === "stale" ? (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">Ikke bekræftet længe</span>
        ) : (
          <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">Kontrollen fejlede</span>
        )}
      </div>

      {k.udfald === "changed" ? (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 text-center">
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs text-slate-500">Nu (aktiv)</p>
              <p className="text-lg font-semibold tabular-nums">{tal(k.gammel_vaerdi)} {k.dokument.enhed}</p>
            </div>
            <div className="rounded-md bg-amber-50 p-3">
              <p className="text-xs text-amber-800">Fundet på kildesiden</p>
              <p className="text-lg font-semibold tabular-nums text-amber-950">{tal(k.ny_vaerdi)} {k.dokument.enhed}</p>
            </div>
          </div>
          {k.uddrag && (
            <blockquote className="mt-3 rounded-md border-l-4 border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-700">
              {k.uddrag}
            </blockquote>
          )}
          <p className="mt-2 text-xs text-slate-500">
            {k.metode === "model"
              ? "Tallet blev læst af Svend, fordi den faste regel ikke kunne afgøre det entydigt – tjek det ekstra grundigt."
              : "Tallet blev fundet med den faste regel ud fra hintet."}
            {k.dokument.kontrol_kilde && <> · <a href={k.dokument.kontrol_kilde} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline">Åbn kildesiden ↗</a></>}
          </p>
        </>
      ) : (
        <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
          {k.fejl} · Den nuværende sats er stadig aktiv, men regnes ikke som bekræftet.
          {k.dokument.kontrol_kilde && <> <a href={k.dokument.kontrol_kilde} target="_blank" rel="noreferrer" className="underline">Åbn kildesiden ↗</a></>}
        </p>
      )}

      {k.afgoerelse ? (
        k.note && <p className="mt-3 text-sm text-slate-600">Note: {k.note}</p>
      ) : (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          {k.udfald === "changed" && (
            <label className="block text-sm">
              <span className="font-medium text-slate-700">Den nye sats gælder fra</span>
              <input type="date" value={gyldigFra} onChange={(e) => setGyldigFra(e.target.value)}
                     className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" />
              <span className="mt-1 block text-xs text-slate-500">Den nuværende sats afløses dagen før.</span>
            </label>
          )}
          <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note"
                    placeholder={k.udfald === "changed" ? "Note (krævet ved afvisning)" : "Hvad er gjort? (krævet)"}
                    className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" />
          {fejl && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{fejl}</p>}
          <div className="flex flex-wrap gap-2">
            {k.udfald === "changed" && (
              <button type="button" disabled={!gyldigFra}
                      onClick={() => udfoer(`/api/v1/kb/kontroller/${k.id}/godkend`, { gyldig_fra: gyldigFra, note: note || null })}
                      className="rounded-md bg-brand-600 px-4 py-1.5 text-sm text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-brand-600/40">
                Godkend ny sats
              </button>
            )}
            <button type="button" disabled={!note.trim()}
                    onClick={() => udfoer(`/api/v1/kb/kontroller/${k.id}/afvis`, { note })}
                    className="rounded-md border border-slate-300 px-4 py-1.5 text-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50">
              {k.udfald === "changed" ? "Afvis ny værdi" : "Kvittér"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Satskontrol() {
  const [alle, setAlle] = useState(false);
  const [liste, setListe] = useState<Kontrol[] | null>(null);
  const [besked, setBesked] = useState<string | null>(null);
  const hentListe = useCallback(() => {
    hent<Kontrol[]>(`/api/v1/kb/kontroller?aabne=${!alle}`).then(setListe);
  }, [alle]);
  useEffect(() => { hentListe(); }, [hentListe]);

  async function koerNu() {
    await send("/api/v1/kb/kontroller/koer");
    setBesked("Kontrollen er lagt i kø og kører om lidt. Opdater siden om et minut.");
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-3xl text-sm text-slate-600">
          Hver mandag kl. 6 efterses alle aktive satser med en kontrolside. Uændrede satser bekræftes automatisk.
          Nye værdier og fejl står her – en ny sats bliver først aktiv, når du godkender den.
        </p>
        <div className="flex items-center gap-3">
          <label className="text-sm text-slate-600"><input type="checkbox" checked={alle} onChange={(e) => setAlle(e.target.checked)} className="mr-1" />Vis også afgjorte</label>
          <button type="button" onClick={koerNu} className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50">Kør kontrol nu</button>
        </div>
      </div>
      {besked && <p role="status" className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{besked}</p>}
      {!liste ? <p className="text-slate-500">Henter …</p> : liste.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
          {alle ? "Ingen kontroller endnu." : "Intet at tage stilling til – alle satser er uændrede eller afgjort."}
        </p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">{liste.map((k) => <Kort key={k.id} k={k} opdater={hentListe} />)}</div>
      )}
    </div>
  );
}
