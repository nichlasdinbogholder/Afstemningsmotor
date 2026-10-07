"use client";

import { useEffect, useRef, useState } from "react";
import { hent, send, type Job } from "@/lib/api";

// "Opdater nu": henter frisk data fra kundens e-conomic og kører reglerne – forrest i køen.
export default function OpdaterKnap({ kundeId, aktiv, naarFaerdig }: { kundeId: number; aktiv: boolean; naarFaerdig: () => void }) {
  const [jobId, setJobId] = useState<number | null>(null);
  const [besked, setBesked] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  async function start() {
    setBesked(null);
    try {
      const r = await send<{ job_id: number }>(`/api/kunder/${kundeId}/opdater`);
      setJobId(r.job_id);
      timer.current = setInterval(async () => {
        const j = await hent<Job>(`/api/jobs/${r.job_id}`);
        if (j.status === "faerdig" || j.status === "fejlet") {
          if (timer.current) clearInterval(timer.current);
          setJobId(null);
          setBesked(j.status === "faerdig" ? "Opdateret" : "Opdateringen fejlede – prøv igen senere");
          naarFaerdig();
        }
      }, 2000);
    } catch (e) {
      setBesked(e instanceof Error ? e.message : "Fejl");
    }
  }

  return (
    <div className="flex items-center justify-end gap-3">
      {besked && <span className="text-sm text-slate-600">{besked}</span>}
      <button onClick={start} disabled={!aktiv || jobId !== null}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400">
        {jobId !== null ? "Henter fra e-conomic …" : "Opdater nu"}
      </button>
    </div>
  );
}
