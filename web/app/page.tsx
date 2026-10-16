"use client";

import { useMig } from "@/components/Ramme";

// Dashboardet er det første, man ser efter login. Hvad det skal vise, er endnu ikke besluttet.
export default function Dashboard() {
  const mig = useMig();
  const fornavn = mig?.navn?.split(" ")[0];
  return (
    <div>
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <p className="mt-1 text-sm text-slate-500">Velkommen{fornavn ? `, ${fornavn}` : ""}.</p>
      <p className="mt-1 text-sm text-slate-600">Her er et overblik over dine opgaver og kommende frister for dine kunder.</p>
      <p className="mt-6 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
        Her kommer overblikket. Indholdet aftales næste gang.
      </p>
    </div>
  );
}
