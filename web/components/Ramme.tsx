"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState } from "react";
import { hent, IkkeLoggetInd, type Medarbejder } from "@/lib/api";

const MigKontekst = createContext<Medarbejder | null>(null);
export const useMig = () => useContext(MigKontekst);

export default function Ramme({ children }: { children: React.ReactNode }) {
  const [mig, setMig] = useState<Medarbejder | null>(null);
  const [tilstand, setTilstand] = useState<"henter" | "ude" | "inde" | "fejl">("henter");

  useEffect(() => {
    hent<Medarbejder>("/api/mig")
      .then((m) => { setMig(m); setTilstand("inde"); })
      .catch((e) => setTilstand(e instanceof IkkeLoggetInd ? "ude" : "fejl"));
  }, []);

  if (tilstand === "henter") return <div className="p-8 text-slate-500">Henter …</div>;
  if (tilstand === "fejl")
    return <div className="p-8 text-red-700">Kunne ikke kontakte serveren. Prøv at genindlæse siden.</div>;
  if (tilstand === "ude")
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
        <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-slate-900">Afstemningsmotor</h1>
          <p className="mt-2 text-sm text-slate-600">Log ind med din Microsoft-konto (samme som Outlook).</p>
          <a href="/login" className="mt-6 inline-block w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">
            Log ind med Microsoft
          </a>
        </div>
      </main>
    );

  return (
    <MigKontekst.Provider value={mig}>
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <Link href="/" className="font-semibold text-slate-900">Din Bogholder</Link>
            <nav className="flex gap-1 text-sm">
              <Link href="/" className="rounded-md px-3 py-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900">Afstemning</Link>
              <Link href="/debitorstyring/" className="rounded-md px-3 py-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900">Debitorstyring</Link>
            </nav>
          </div>
          <div className="flex items-center gap-4 text-sm text-slate-600">
            <span>{mig?.navn}{mig?.rolle === "admin" && <span className="ml-1 text-slate-400">(administrator)</span>}</span>
            <a href="/logout" className="text-slate-500 hover:text-slate-900">Log ud</a>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
    </MigKontekst.Provider>
  );
}
