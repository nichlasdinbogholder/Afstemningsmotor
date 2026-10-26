"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { hent, send } from "@/lib/api";
import { tidspunkt } from "@/lib/format";

// Notifikationer (bygherren 26.10.2026): klokken i øverste højre hjørne, som medarbejderen holder øje med hver dag.
// Kun ens egne notifikationer; alt afgøres på serveren (/api/notifikationer).
type Notifikation = {
  id: number; alvor: "info" | "advarsel" | "kritisk"; titel: string; tekst: string | null; link: string | null;
  oprettet: string | null; laest: string | null;
};
type Svar = { ulaeste: number; notifikationer: Notifikation[] };

const FARVE = { kritisk: "bg-red-600", advarsel: "bg-amber-500", info: "bg-slate-400" };
const HVER_5_MINUT = 5 * 60 * 1000;

export default function Klokke() {
  const [data, setData] = useState<Svar | null>(null);
  const [aaben, setAaben] = useState(false);
  const boks = useRef<HTMLDivElement>(null);
  const router = useRouter();

  const opdater = useCallback(() => { hent<Svar>("/api/notifikationer").then(setData).catch(() => {}); }, []);
  useEffect(() => {
    opdater();
    const t = setInterval(opdater, HVER_5_MINUT);
    return () => clearInterval(t);
  }, [opdater]);
  useEffect(() => {
    if (!aaben) return;
    const luk = (e: MouseEvent) => { if (boks.current && !boks.current.contains(e.target as Node)) setAaben(false); };
    document.addEventListener("mousedown", luk);
    return () => document.removeEventListener("mousedown", luk);
  }, [aaben]);

  async function vaelg(n: Notifikation) {
    if (!n.laest) await send(`/api/notifikationer/${n.id}/laest`).catch(() => {});
    setAaben(false);
    opdater();
    if (n.link) router.push(n.link);
  }

  const ulaeste = data?.ulaeste ?? 0;
  return (
    <div ref={boks} className="relative">
      <button type="button" onClick={() => { setAaben(!aaben); if (!aaben) opdater(); }}
              aria-label={ulaeste ? `Notifikationer – ${ulaeste} ulæste` : "Notifikationer"} aria-expanded={aaben}
              className="relative rounded-full p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-900">
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {ulaeste > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-brand-600 px-1 text-center text-[10px] font-semibold leading-4 text-white">
            {ulaeste > 99 ? "99+" : ulaeste}
          </span>
        )}
      </button>
      {aaben && (
        <div className="absolute right-0 top-full z-[1100] mt-2 w-96 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
            <p className="font-semibold text-slate-900">Notifikationer</p>
            {ulaeste > 0 && (
              <button type="button" onClick={() => send("/api/notifikationer/laest").then(opdater).catch(() => {})}
                      className="text-xs text-brand-600 hover:underline">Markér alle som læst</button>
            )}
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {(data?.notifikationer ?? []).map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => vaelg(n)}
                        className={`flex w-full gap-3 border-b border-slate-50 px-4 py-2.5 text-left hover:bg-slate-50 ${n.laest ? "opacity-60" : ""}`}>
                  <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${FARVE[n.alvor]}`} aria-label={n.alvor} />
                  <span className="min-w-0">
                    <span className={`block text-sm ${n.laest ? "text-slate-600" : "font-medium text-slate-900"}`}>{n.titel}</span>
                    {n.tekst && <span className="block text-xs text-slate-500">{n.tekst}</span>}
                    {n.oprettet && <span className="block text-xs text-slate-400">{tidspunkt(n.oprettet)}</span>}
                  </span>
                </button>
              </li>
            ))}
            {data && data.notifikationer.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-slate-500">Ingen notifikationer</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
