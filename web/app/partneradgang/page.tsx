"use client";

import Pladsholder from "@/components/Pladsholder";
import { useMig } from "@/components/Ramme";

// Partneradgang er KUN for administratorer (bygherren 23.10.2026). Når siden får indhold, skal dens API også
// kræve admin (Depends(kraev_admin)) – det her skjuler kun siden for andre.
export default function Side() {
  const mig = useMig();
  if (!mig) return <p className="text-slate-500">Henter …</p>;
  if (mig.rolle !== "admin") return <p className="text-slate-600">Kun administratorer har adgang til Partneradgang.</p>;
  return <Pladsholder titel="Partneradgang" />;
}
