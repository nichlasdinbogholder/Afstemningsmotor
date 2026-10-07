"use client";

import { useEffect, useState } from "react";
import { hent, type Kontoudtog, type UdtogLinje } from "@/lib/api";
import { beloeb, dato, KILDE } from "@/lib/format";

export default function KontoudtogListe({ kundeId, version }: { kundeId: number; version: number }) {
  const [udtog, setUdtog] = useState<Kontoudtog[] | null>(null);
  const [aaben, setAaben] = useState<number | null>(null);

  useEffect(() => { hent<Kontoudtog[]>(`/api/kunder/${kundeId}/kontoudtog`).then(setUdtog); }, [kundeId, version]);

  if (!udtog) return <p className="text-slate-500">Henter kontoudtog …</p>;
  if (udtog.length === 0)
    return <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">Der er ikke indlæst kontoudtog for kunden.</p>;

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2">Periode</th>
            <th className="px-4 py-2">Kilde</th>
            <th className="px-4 py-2">Modstykke</th>
            <th className="px-4 py-2 text-right">Linjer</th>
            <th className="px-4 py-2 text-right">Matchet</th>
            <th className="px-4 py-2">Fil</th>
          </tr>
        </thead>
        <tbody>
          {udtog.map((u) => (
            <Raekke key={u.id} u={u} aaben={aaben === u.id} skift={() => setAaben(aaben === u.id ? null : u.id)} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Raekke({ u, aaben, skift }: { u: Kontoudtog; aaben: boolean; skift: () => void }) {
  const pct = u.matchprocent ?? 0;
  return (
    <>
      <tr onClick={skift} className="cursor-pointer border-b border-slate-100 hover:bg-slate-50">
        <td className="px-4 py-2">{aaben ? "▾" : "▸"} {dato(u.periode_fra)} – {dato(u.periode_til)}</td>
        <td className="px-4 py-2">{KILDE[u.kilde] ?? u.kilde}</td>
        <td className="px-4 py-2">{u.modpart_navn ?? u.modpart ?? `konto ${u.kontonummer}`}</td>
        <td className="px-4 py-2 text-right">{u.linjer}</td>
        <td className="px-4 py-2 text-right">
          <span className={pct >= 95 ? "text-emerald-700" : pct >= 75 ? "text-amber-700" : "text-red-700"}>
            {u.matchet} ({u.matchprocent ?? 0} %)
          </span>
        </td>
        <td className="px-4 py-2 text-slate-500">{u.kildefil}</td>
      </tr>
      {aaben && (
        <tr className="border-b border-slate-200 bg-slate-50">
          <td colSpan={6} className="px-4 py-3"><Linjer id={u.id} /></td>
        </tr>
      )}
    </>
  );
}

const MATCH: Record<string, string> = { bilag_beloeb: "Fakturanr. + beløb", beloeb_dato: "Beløb + dato" };

function Linjer({ id }: { id: number }) {
  const [linjer, setLinjer] = useState<UdtogLinje[] | null>(null);
  useEffect(() => { hent<{ linjer: UdtogLinje[] }>(`/api/kontoudtog/${id}`).then((d) => setLinjer(d.linjer)); }, [id]);
  if (!linjer) return <p className="text-slate-500">Henter linjer …</p>;
  return (
    <table className="w-full text-xs">
      <thead className="text-left text-slate-500">
        <tr>
          <th className="py-1 pr-3">Dato</th><th className="pr-3">Reference</th><th className="pr-3">Tekst</th>
          <th className="pr-3 text-right">Beløb</th><th className="pr-3">Match</th><th>I bogføringen</th>
        </tr>
      </thead>
      <tbody>
        {linjer.map((l) => (
          <tr key={l.nr} className={l.match ? "" : "text-red-800"}>
            <td className="py-1 pr-3">{dato(l.dato)}</td>
            <td className="pr-3">{l.reference}</td>
            <td className="pr-3">{l.tekst}</td>
            <td className="pr-3 text-right tabular-nums">{beloeb(l.beloeb)}</td>
            <td className="pr-3">{l.match ? MATCH[l.match] : <strong>Mangler i bogføring</strong>}</td>
            <td>{l.bogfoert && `bilag ${l.bogfoert.bilag ?? "–"}, ${dato(l.bogfoert.dato)}, ${beloeb(l.bogfoert.beloeb)}`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
