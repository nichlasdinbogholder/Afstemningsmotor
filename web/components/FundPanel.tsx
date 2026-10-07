"use client";

import { useEffect, useState } from "react";
import { AlvorMaerke, StatusMaerke } from "@/components/Maerker";
import { hent, send, type FundDetalje, type Status } from "@/lib/api";
import { dato, STATUS, tidspunkt } from "@/lib/format";

const HANDLINGER: { status: Status; tekst: string; note: boolean }[] = [
  { status: "resolved", tekst: "Løst", note: false },
  { status: "accepted", tekst: "Godkend", note: true },
  { status: "ignored", tekst: "Ignorér", note: true },
  { status: "open", tekst: "Genåbn", note: false },
];

export default function FundPanel({ fundId, luk, aendret }: { fundId: number; luk: () => void; aendret: () => void }) {
  const [f, setF] = useState<FundDetalje | null>(null);
  const [note, setNote] = useState("");
  const [fejl, setFejl] = useState<string | null>(null);
  const [gemmer, setGemmer] = useState(false);

  // Komponenten får key={fundId}, så den starter forfra for hvert fund.
  useEffect(() => { hent<FundDetalje>(`/api/fund/${fundId}`).then(setF); }, [fundId]);

  async function saet(status: Status, kraeverNote: boolean) {
    if (kraeverNote && !note.trim()) { setFejl("Skriv en note – hvorfor godkendes eller ignoreres fundet?"); return; }
    setGemmer(true); setFejl(null);
    try {
      await send(`/api/fund/${fundId}/status`, { status, note: note.trim() || null });
      setF(await hent<FundDetalje>(`/api/fund/${fundId}`));
      setNote("");
      aendret();
    } catch (e) {
      setFejl(e instanceof Error ? e.message : "Fejl");
    } finally {
      setGemmer(false);
    }
  }

  return (
    <aside className="sticky top-4 h-fit max-h-[calc(100vh-2rem)] w-[28rem] shrink-0 overflow-y-auto rounded-lg border border-slate-200 bg-white p-5">
      <div className="mb-3 flex items-start justify-between gap-2">
        {f ? <div className="flex gap-2"><AlvorMaerke alvor={f.alvor} /><StatusMaerke status={f.status} /></div> : <span />}
        <button onClick={luk} className="text-slate-400 hover:text-slate-900" aria-label="Luk">✕</button>
      </div>
      {!f ? <p className="text-slate-500">Henter …</p> : (
        <>
          <h2 className="font-semibold text-slate-900">{f.titel}</h2>
          <p className="mt-1 text-xs text-slate-500">
            {f.regel_navn} · fundet første gang {dato(f.foerst_set)}{!f.aktuel && " · optræder ikke længere i seneste kørsel"}
          </p>

          <div className="mt-4 space-y-2">
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
                      placeholder="Note (skal udfyldes ved Godkend og Ignorér)"
                      className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
            <div className="flex flex-wrap gap-2">
              {HANDLINGER.filter((h) => h.status !== f.status).map((h) => (
                <button key={h.status} disabled={gemmer} onClick={() => saet(h.status, h.note)}
                        className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-50">
                  {h.tekst}
                </button>
              ))}
            </div>
            {fejl && <p className="text-sm text-red-700">{fejl}</p>}
          </div>

          <h3 className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Detaljer</h3>
          <Detaljer data={f.detaljer} />

          <h3 className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Historik</h3>
          <ul className="space-y-2 text-sm">
            {f.historik.map((h, i) => (
              <li key={i} className="border-l-2 border-slate-200 pl-3">
                <span className="text-slate-900">{h.fra ? `${STATUS[h.fra as Status] ?? h.fra} → ` : ""}{STATUS[h.til as Status] ?? h.til}</span>
                <span className="block text-xs text-slate-500">{h.af} · {tidspunkt(h.tidspunkt)}</span>
                {h.note && <span className="block text-slate-700">{h.note}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}

function Vaerdi({ v }: { v: unknown }) {
  if (v === null || v === undefined || v === "") return <span className="text-slate-400">–</span>;
  if (typeof v === "object") return <Detaljer data={v as Record<string, unknown>} />;
  return <span>{String(v)}</span>;
}

function Detaljer({ data }: { data: Record<string, unknown> | unknown[] }) {
  if (Array.isArray(data)) {
    const raekker = data.filter((r): r is Record<string, unknown> => !!r && typeof r === "object");
    if (raekker.length !== data.length || raekker.length === 0) return <span>{data.map(String).join(", ")}</span>;
    const kolonner = Array.from(new Set(raekker.flatMap((r) => Object.keys(r))));
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr>{kolonner.map((k) => <th key={k} className="pr-3 text-left font-medium text-slate-500">{k.replaceAll("_", " ")}</th>)}</tr></thead>
          <tbody>{raekker.map((r, i) => <tr key={i}>{kolonner.map((k) => <td key={k} className="pr-3 align-top"><Vaerdi v={r[k]} /></td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  }
  if (Object.keys(data).length === 0) return <p className="text-sm text-slate-400">Ingen detaljer.</p>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      {Object.entries(data).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{k.replaceAll("_", " ")}</dt>
          <dd className="min-w-0 break-words text-slate-900"><Vaerdi v={v} /></dd>
        </div>
      ))}
    </dl>
  );
}
