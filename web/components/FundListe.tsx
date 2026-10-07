"use client";

import { useEffect, useState } from "react";
import FundPanel from "@/components/FundPanel";
import { AlvorMaerke, StatusMaerke } from "@/components/Maerker";
import { hent, type Fund, type Status } from "@/lib/api";
import { dato, STATUS } from "@/lib/format";

type Svar = { antal: number; fund: Fund[]; afkortet: boolean };

export default function FundListe({ kundeId, version }: { kundeId: number; version: number }) {
  const [status, setStatus] = useState<Status | "">("open");
  const [regel, setRegel] = useState("");
  const [svar, setSvar] = useState<Svar | null>(null);
  const [valgt, setValgt] = useState<number | null>(null);
  const [genhent, setGenhent] = useState(0);

  useEffect(() => {
    const p = new URLSearchParams({ status });
    hent<Svar>(`/api/kunder/${kundeId}/fund?${p}`).then(setSvar);
  }, [kundeId, status, version, genhent]);

  const regler = Array.from(new Map((svar?.fund ?? []).map((f) => [f.regel, f.regel_navn])));
  const viste = (svar?.fund ?? []).filter((f) => !regel || f.regel === regel);

  return (
    <div className="flex gap-4">
      <div className="min-w-0 flex-1">
        <div className="mb-3 flex flex-wrap gap-3">
          <select value={status} onChange={(e) => setStatus(e.target.value as Status | "")}
                  className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm">
            {(Object.keys(STATUS) as Status[]).map((s) => <option key={s} value={s}>{STATUS[s]}</option>)}
            <option value="">Alle statusser</option>
          </select>
          <select value={regel} onChange={(e) => setRegel(e.target.value)}
                  className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm">
            <option value="">Alle typer fund</option>
            {regler.map(([kode, navn]) => <option key={kode} value={kode}>{navn}</option>)}
          </select>
          <span className="self-center text-sm text-slate-500">{viste.length} fund{svar?.afkortet && " (viser de første 500)"}</span>
        </div>

        {!svar ? <p className="text-slate-500">Henter fund …</p> : viste.length === 0 ? (
          <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">Ingen fund her.</p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {viste.map((f) => (
              <li key={f.id}>
                <button onClick={() => setValgt(f.id)}
                        className={`flex w-full items-start gap-3 px-4 py-3 text-left text-sm hover:bg-slate-50 ${valgt === f.id ? "bg-slate-100" : ""}`}>
                  <AlvorMaerke alvor={f.alvor} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-slate-900">{f.titel}</span>
                    <span className="text-xs text-slate-500">{f.regel_navn} · {dato(f.periode_fra ?? f.foerst_set)}</span>
                  </span>
                  <StatusMaerke status={f.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {valgt !== null && (
        <FundPanel key={valgt} fundId={valgt} luk={() => setValgt(null)} aendret={() => setGenhent((n) => n + 1)} />
      )}
    </div>
  );
}
