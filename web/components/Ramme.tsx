"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { hent, IkkeLoggetInd, type Medarbejder } from "@/lib/api";
import Fagassistent from "@/components/assistent/Fagassistent";
import Klokke from "@/components/Klokke";
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

// Hub'ens dele i venstre side – én boks pr. del. Siderne er pladsholdere, indtil indholdet er aftalt.
// Kunde har undermenuer (bygherren 23.10.2026) – de vises i en dropdown under boksen.
const KUNDE_UNDERMENU = [
  { href: "/kunder/", tekst: "Kundeoversigt" },
  { href: "/kunder/arkiverede/", tekst: "Arkiverede kunder" },
];

const DELE = [
  { href: "/ai-planlaegning/", tekst: "AI Planlægning" },
  { href: "/opgavestyring/", tekst: "Opgavestyring" },
  { href: "/controlling/", tekst: "Controlling" },
  { href: "/loenadministration/", tekst: "Lønadministration" },
  { href: "/salg/", tekst: "Salg" },
  { href: "/tickets/", tekst: "Tickets" },
  // Kun for administratorer (bygherren 23.10.2026) – vises ikke for andre; siden tjekker det også.
  { href: "/partneradgang/", tekst: "Partneradgang", kunAdmin: true },
];

// Boksen "Kunde" folder sig ud med undermenuerne. Den står åben, når man er på en af kundesiderne.
function Kundemenu({ sti }: { sti: string }) {
  const paaSiden = sti.startsWith("/kunder");
  const [klikket, setKlikket] = useState<boolean | null>(null);
  const aaben = klikket ?? paaSiden;
  return (
    <div className={`rounded-lg border bg-white text-sm ${paaSiden ? "border-brand-600 shadow-sm" : "border-slate-200"}`}>
      <button type="button" onClick={() => setKlikket(!aaben)} aria-expanded={aaben}
              className={`flex w-full items-center justify-between px-3 py-2.5 text-left ${paaSiden ? "font-medium text-slate-900" : "text-slate-700 hover:text-slate-900"}`}>
        Kunde
        <span aria-hidden className={`text-xs text-slate-400 transition-transform ${aaben ? "rotate-180" : ""}`}>▾</span>
      </button>
      {aaben && (
        <ul className="border-t border-slate-100 px-2 py-1.5 text-xs">
          {KUNDE_UNDERMENU.map((u) => {
            const aktiv = sti.replace(/\/$/, "") === u.href.replace(/\/$/, "");
            return (
              <li key={u.href}>
                <Link href={u.href}
                      className={`block rounded-md border-l-2 px-2 py-1.5 ${aktiv ? "border-brand-600 bg-slate-50 font-medium text-slate-900" : "border-transparent text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
                  {u.tekst}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Venstremenu({ sti, admin }: { sti: string; admin: boolean }) {
  return (
    <nav aria-label="Hub'ens dele" className="sticky top-4 space-y-2 px-4 pt-6">
      <Kundemenu sti={sti} />
      {DELE.filter((d) => admin || !("kunAdmin" in d)).map((d) => {
        const aktiv = sti.startsWith(d.href.replace(/\/$/, ""));
        return (
          <Link key={d.href} href={d.href}
                className={`block rounded-lg border bg-white px-3 py-2.5 text-sm ${aktiv ? "border-brand-600 font-medium text-slate-900 shadow-sm" : "border-slate-200 text-slate-700 hover:border-slate-300 hover:text-slate-900"}`}>
            {d.tekst}
          </Link>
        );
      })}
    </nav>
  );
}

// INDSTILLINGER nederst i venstre hjørne: menuer om selve hub'en. Flere punkter kommer senere.
// Den ligger i sin egen smalle kolonne, så den aldrig dækker tabeller. (Hub'en bruges kun på PC – ikke telefon.)
function Indstillinger({ sti, admin, className }: { sti: string; admin: boolean; className: string }) {
  const punkter = [
    { href: "/fagassistent/", tekst: "Svend", aktiv: sti.startsWith("/fagassistent") },
    // Medarbejdere ses kun af administratorer (siden og API'et kræver også admin).
    ...(admin ? [{ href: "/medarbejdere/", tekst: "Medarbejdere", aktiv: sti.startsWith("/medarbejdere") }] : []),
  ];
  return (
    <nav aria-label="Indstillinger" className={`rounded-lg border border-slate-200 bg-white p-3 text-sm ${className}`}>
      <p className="mb-1 px-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Indstillinger</p>
      <ul>
        {punkter.map((p) => (
          <li key={p.href}>
            <Link href={p.href}
                  className={`block rounded-md border-l-2 px-2 py-1.5 ${p.aktiv ? "border-brand-600 bg-slate-50 font-medium text-slate-900" : "border-transparent text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
              {p.tekst}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
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
              <Menupunkt href="/timeregistrering/" aktiv={sti.startsWith("/timeregistrering")}>Timeregistrering</Menupunkt>
              <Menupunkt href="/afstemning/" aktiv={sti.startsWith("/afstemning") || /^\/kunde\/?$/.test(sti)}>Afstemning</Menupunkt>
              <Menupunkt href="/debitorstyring/" aktiv={sti.startsWith("/debitorstyring")}>Debitorstyring</Menupunkt>
            </nav>
          </div>
          <div className="flex items-center gap-4 text-sm text-slate-600">
            <Klokke />
            <span>{mig?.navn}{mig?.rolle === "admin" && <span className="ml-1 text-slate-400">(administrator)</span>}</span>
            <a href="/logout" className="text-slate-500 hover:text-brand-600">Log ud</a>
          </div>
        </div>
      </header>
      <AssistentProvider>
        <div className="flex">
          {/* Venstre kolonne: hub'ens dele øverst; INDSTILLINGER-boksen er fastgjort i hjørnet nederst. */}
          <div className="w-52 shrink-0"><Venstremenu sti={sti} admin={mig?.rolle === "admin"} /></div>
          {/* pb-28: så det nederste på siden ikke skjules bag Svends knap */}
          <main className="mx-auto min-w-0 max-w-7xl flex-1 px-4 pb-28 pt-6">{children}</main>
        </div>
        <Indstillinger sti={sti} admin={mig?.rolle === "admin"} className="fixed bottom-6 left-4 z-[900] w-44" />
        {/* Fagassistenten ligger her (i rodlayoutet) – så overlever den navigation mellem siderne. */}
        <Fagassistent />
      </AssistentProvider>
    </MigKontekst.Provider>
  );
}
