"use client";

import Link from "next/link";
import { useMig } from "@/components/Ramme";

// Dashboardet er det første, man ser efter login. Hvad det skal vise, er endnu ikke besluttet –
// indtil da er her kun genveje til hubbens dele.
const GENVEJE = [
  { href: "/afstemning/", titel: "Afstemning", tekst: "Kunder, fund og kontoudtog" },
  { href: "/debitorstyring/", titel: "Debitorstyring", tekst: "Fakturaer, rykkere og afbetalinger" },
];

export default function Dashboard() {
  const mig = useMig();
  const fornavn = mig?.navn?.split(" ")[0];
  return (
    <div>
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <p className="mt-1 text-sm text-slate-500">Velkommen{fornavn ? `, ${fornavn}` : ""}.</p>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {GENVEJE.map((g) => (
          <Link key={g.href} href={g.href}
                className="rounded-lg border border-slate-200 border-l-4 border-l-brand-600 bg-white p-5 hover:shadow-sm">
            <p className="font-semibold text-slate-900">{g.titel} →</p>
            <p className="mt-1 text-sm text-slate-500">{g.tekst}</p>
          </Link>
        ))}
      </div>
      <p className="mt-8 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
        Her kommer overblikket. Indholdet aftales næste gang.
      </p>
    </div>
  );
}
