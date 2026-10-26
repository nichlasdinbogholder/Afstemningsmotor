"use client";

import { useEffect, useState } from "react";
import { hent } from "@/lib/api";
import { tidspunkt } from "@/lib/format";
import Satskontrol from "@/components/assistent/Satskontrol";
import Videnbase from "@/components/assistent/Videnbase";
import { useMig } from "@/components/Ramme";
import AdminLaas from "@/components/AdminLaas";

// Loggen over spørgsmål til fagassistenten. Administratorer ser alle; en medarbejder ser kun sine egne.
// Listen viser, hvor fagligheden halter – gennemgå den ugentligt den første måned.

type Raekke = {
  id: number; tidspunkt: string; medarbejder: string; email: string; spoergsmaal: string;
  svar: string | null; svar_status: string | null; vurdering: string | null; kommentar: string | null;
};
type Log = { alle: boolean; spoergsmaal: Raekke[]; afkortet: boolean };
type Emne = { ord: string; antal: number };

export default function Side() {
  // ?fane=videnbase&dok=<id> åbner et dokument direkte (bruges af kilderne under Svends svar).
  const mig = useMig();
  const [fane, setFane] = useState<"spoergsmaal" | "videnbase" | "satskontrol">(() => {
    const f = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("fane") : null;
    return f === "videnbase" || f === "satskontrol" ? f : "spoergsmaal";
  });
  const [dokId] = useState<number | null>(() => {
    const v = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("dok") : null;
    return v && /^\d+$/.test(v) ? Number(v) : null;
  });
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Svend</h1>
        <div className="mt-3 flex gap-1 border-b border-slate-200">
          {[{ id: "spoergsmaal" as const, t: "Spørgsmål" }, { id: "videnbase" as const, t: "Videnbase" },
            ...(mig?.rolle === "admin" ? [{ id: "satskontrol" as const, t: "Satskontrol" }] : [])].map((f) => (
            <button key={f.id} type="button" onClick={() => setFane(f.id)}
                    className={`-mb-px border-b-2 px-4 py-2 text-sm ${fane === f.id ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900"}`}>
              {f.t}{f.id === "satskontrol" && <AdminLaas className="ml-1.5" />}
            </button>
          ))}
        </div>
      </div>
      {fane === "spoergsmaal" ? <Spoergsmaal /> : fane === "videnbase" ? <Videnbase start={dokId} />
        : mig?.rolle === "admin" ? <Satskontrol /> : null}
    </div>
  );
}

function Spoergsmaal() {
  const [kunNed, setKunNed] = useState(false);
  const [log, setLog] = useState<Log | null>(null);
  const [emner, setEmner] = useState<Emne[]>([]);
  const [aaben, setAaben] = useState<number | null>(null);

  useEffect(() => {
    hent<Log>(`/api/v1/assistant/log?kun_ned=${kunNed}`).then(setLog);
  }, [kunNed]);
  useEffect(() => {
    hent<Emne[]>("/api/v1/assistant/emner").then(setEmner);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <p className="max-w-3xl text-sm text-slate-600">
          {log?.alle ? "Alle spørgsmål til Svend, vores fagassistent, nyeste først." : "Dine spørgsmål til Svend, vores fagassistent, nyeste først."}{" "}
          Svar med tommel ned viser, hvor Svend – eller vores materiale – skal forbedres.
        </p>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-sm font-semibold text-slate-700">Hyppigste ord i spørgsmålene – seneste 30 dage</p>
        {emner.length === 0 ? <p className="mt-2 text-sm text-slate-500">Ingen spørgsmål endnu.</p> : (
          <div className="mt-3 flex flex-wrap gap-2">
            {emner.map((e) => (
              <span key={e.ord} className="rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700">
                {e.ord} <span className="text-slate-400">{e.antal}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="flex gap-1 border-b border-slate-200 pb-2">
        {[{ v: false, t: "Alle spørgsmål" }, { v: true, t: "Kun tommel ned" }].map((f) => (
          <button key={String(f.v)} type="button" onClick={() => setKunNed(f.v)}
                  className={`rounded-md px-3 py-1.5 text-sm ${kunNed === f.v ? "bg-slate-200 font-medium text-slate-900" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"}`}>
            {f.t}
          </button>
        ))}
      </div>

      {!log ? <p className="text-slate-500">Henter …</p> : log.spoergsmaal.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">Ingen spørgsmål her.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr>
                <th className="px-3 py-2">Tidspunkt</th>
                {log.alle && <th className="px-3 py-2">Medarbejder</th>}
                <th className="px-3 py-2">Spørgsmål</th>
                <th className="px-3 py-2">Vurdering</th>
              </tr>
            </thead>
            <tbody>
              {log.spoergsmaal.map((r) => (
                <tr key={r.id} onClick={() => setAaben(aaben === r.id ? null : r.id)}
                    className="cursor-pointer border-b border-slate-100 align-top last:border-0 hover:bg-slate-50">
                  <td className="whitespace-nowrap px-3 py-2 text-slate-500">{tidspunkt(r.tidspunkt)}</td>
                  {log.alle && <td className="whitespace-nowrap px-3 py-2">{r.medarbejder}</td>}
                  <td className="px-3 py-2">
                    <p className="text-slate-900">{r.spoergsmaal}</p>
                    {aaben === r.id && (
                      <div className="mt-2 whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-slate-700">
                        {r.svar ?? <span className="text-slate-400">Intet svar gemt</span>}
                        {r.svar_status === "interrupted" && <p className="mt-1 text-xs text-slate-400">(Svaret blev afbrudt)</p>}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {r.vurdering === "up" && <span>👍</span>}
                    {r.vurdering === "down" && <span>👎{r.kommentar && <span className="ml-2 text-slate-600">{r.kommentar}</span>}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {log.afkortet && <p className="p-3 text-center text-xs text-slate-500">Viser de nyeste 200.</p>}
        </div>
      )}
    </div>
  );
}
