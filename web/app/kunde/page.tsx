"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import FundListe from "@/components/FundListe";
import KontoudtogListe from "@/components/KontoudtogListe";
import OpdaterKnap from "@/components/OpdaterKnap";
import { hent, type Kunde } from "@/lib/api";
import { dato, tidspunkt } from "@/lib/format";

export default function Side() {
  return (
    <Suspense fallback={<p className="text-slate-500">Henter …</p>}>
      <KundeSide />
    </Suspense>
  );
}

function KundeSide() {
  const id = Number(useSearchParams().get("id"));
  const [kunde, setKunde] = useState<Kunde | null>(null);
  const [fejl, setFejl] = useState<string | null>(null);
  const [fane, setFane] = useState<"fund" | "kontoudtog">("fund");
  const [version, setVersion] = useState(0); // stiger efter "Opdater nu" -> listerne henter igen

  const hentKunde = useCallback(() => {
    hent<Kunde>(`/api/kunder/${id}`).then(setKunde).catch((e) => setFejl(String(e.message ?? e)));
  }, [id]);
  useEffect(hentKunde, [hentKunde]);

  if (!id) return <p className="text-red-700">Ingen kunde valgt.</p>;
  if (fejl) return <p className="text-red-700">{fejl}</p>;
  if (!kunde) return <p className="text-slate-500">Henter kunde …</p>;

  const opdateret = kunde.sidst_opdateret;
  return (
    <>
      <Link href="/" className="text-sm text-slate-500 hover:text-slate-900">← Alle kunder</Link>
      <div className="mt-2 mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{kunde.navn}</h1>
          <p className="text-sm text-slate-500">
            Kundenr. {kunde.kundenummer}{kunde.cvr && ` · CVR ${kunde.cvr}`}
            {kunde.kontoudtog_fra && ` · kontoudtog afstemmes fra ${dato(kunde.kontoudtog_fra)}`}
          </p>
        </div>
        <div className="text-right">
          <OpdaterKnap kundeId={kunde.id} aktiv={kunde.status === "aktiv"}
                       naarFaerdig={() => { hentKunde(); setVersion((v) => v + 1); }} />
          <p className="mt-1 text-xs text-slate-500">
            {opdateret
              ? <>Data fra e-conomic: {tidspunkt(opdateret.tidspunkt)} ({opdateret.hvordan}){opdateret.status !== "ok" && <span className="text-red-700"> – noget fejlede</span>}</>
              : "Endnu ikke hentet fra e-conomic"}
          </p>
        </div>
      </div>

      <div className="mb-4 flex gap-1 border-b border-slate-200">
        {(["fund", "kontoudtog"] as const).map((f) => (
          <button key={f} onClick={() => setFane(f)}
                  className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${fane === f ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
            {f === "fund" ? "Fund" : "Kontoudtog"}
          </button>
        ))}
      </div>

      {fane === "fund" ? <FundListe kundeId={kunde.id} version={version} /> : <KontoudtogListe kundeId={kunde.id} version={version} />}
    </>
  );
}
