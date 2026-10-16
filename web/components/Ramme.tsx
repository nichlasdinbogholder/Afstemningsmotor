"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { hent, IkkeLoggetInd, type Medarbejder } from "@/lib/api";
import Fagassistent from "@/components/assistent/Fagassistent";
import { AssistentProvider } from "@/components/assistent/Provider";

const MigKontekst = createContext<Medarbejder | null>(null);
export const useMig = () => useContext(MigKontekst);

// Menupunkt: rød streg under og mørk tekst, når man står på siden (eller en underside).
function Menupunkt({ href, aktiv, children }: { href: string; aktiv: boolean; children: React.ReactNode }) {
  return (
    <Link href={href}
          className={`-mb-px border-b-2 px-3 py-4 ${aktiv ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-600 hover:border-brand-200 hover:text-slate-900"}`}>
      {children}
    </Link>
  );
}

export default function Ramme({ children }: { children: React.ReactNode }) {
  const [mig, setMig] = useState<Medarbejder | null>(null);
  const [tilstand, setTilstand] = useState<"henter" | "ude" | "inde" | "fejl">("henter");
  const sti = usePathname();

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
        <div className="w-full max-w-sm overflow-hidden rounded-lg border border-slate-200 bg-white text-center shadow-sm">
          <div className="h-1.5 bg-brand-600" />
          <div className="p-8">
          <Image src="/logo.png" alt="Din Bogholder – Håndværkernes foretrukne" width={768} height={192} priority className="mx-auto h-auto w-56" />
          <h1 className="mt-6 text-lg font-semibold text-slate-900">Hub</h1>
          <p className="mt-2 text-sm text-slate-600">Log ind med din Microsoft-konto (samme som Outlook).</p>
          <a href="/login" className="mt-6 inline-block w-full rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            Log ind med Microsoft
          </a>
          </div>
        </div>
      </main>
    );

  return (
    <MigKontekst.Provider value={mig}>
      <header className="border-t-4 border-brand-600 border-b border-b-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4">
          <div className="flex items-center gap-8">
            <Link href="/" aria-label="Forside">
              <Image src="/logo.png" alt="Din Bogholder – Håndværkernes foretrukne" width={768} height={192} priority className="h-10 w-auto" />
            </Link>
            <nav className="flex gap-1 text-sm">
              <Menupunkt href="/" aktiv={sti === "/"}>Dashboard</Menupunkt>
              <Menupunkt href="/afstemning/" aktiv={sti.startsWith("/afstemning") || sti.startsWith("/kunde")}>Afstemning</Menupunkt>
              <Menupunkt href="/debitorstyring/" aktiv={sti.startsWith("/debitorstyring")}>Debitorstyring</Menupunkt>
              {mig?.rolle === "admin" && (
                <Menupunkt href="/medarbejdere/" aktiv={sti.startsWith("/medarbejdere")}>Medarbejdere</Menupunkt>
              )}
            </nav>
          </div>
          <div className="flex items-center gap-4 text-sm text-slate-600">
            <span>{mig?.navn}{mig?.rolle === "admin" && <span className="ml-1 text-slate-400">(administrator)</span>}</span>
            <a href="/logout" className="text-slate-500 hover:text-brand-600">Log ud</a>
          </div>
        </div>
      </header>
      <AssistentProvider>
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
        {/* Fagassistenten ligger her (i rodlayoutet) – så overlever den navigation mellem siderne. */}
        <Fagassistent />
      </AssistentProvider>
    </MigKontekst.Provider>
  );
}
