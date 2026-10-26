"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useMig } from "@/components/Ramme";
import { hent, send } from "@/lib/api";
import { dato } from "@/lib/format";
import { afregningTekst, type KartotekKunde } from "@/lib/kartotek";

export default function Side() {
  const mig = useMig();
  const admin = mig?.rolle === "admin";
  const [liste, setListe] = useState<KartotekKunde[] | null>(null);
  const [soeg, setSoeg] = useState("");
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const hentListe = useCallback(() => {
    hent<KartotekKunde[]>("/api/kundekartotek?arkiverede=true").then(setListe);
  }, []);
  useEffect(() => { hentListe(); }, [hentListe]);

  async function genaktiver(k: KartotekKunde) {
    if (!window.confirm(`Genaktivér ${k.navn}? Kunden bliver aktiv igen: data hentes igen fra regnskabssystemet, og ` +
                        "kunden kan vælges i timeregistreringen og hos Svend.")) return;
    try {
      await send(`/api/kundekartotek/${k.id}/genaktiver`);
      setBesked({ tekst: `${k.navn} er genaktiveret og står nu i Kundeoversigten` });
      hentListe();
    } catch (e) {
      setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true });
    }
  }

  const s = soeg.trim().toLowerCase();
  const viste = (liste ?? []).filter((k) => !s || [k.navn, k.kundenummer, k.cvr ?? "", k.aarsag ?? ""]
    .some((v) => v.toLowerCase().includes(s)));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Arkiverede kunder</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          Kunder, der er arkiveret fra <Link href="/kunder/" className="text-brand-700 underline">Kundeoversigten</Link>.
          De er stoppet helt, men intet er slettet – historik, timer og fund er bevaret.
          {admin ? " Du kan genaktivere en kunde, hvis samarbejdet starter igen." : " Kun administratorer kan genaktivere."}
        </p>
      </div>

      {besked && <p className={`rounded-md px-3 py-2 text-sm ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}

      <input value={soeg} onChange={(e) => setSoeg(e.target.value)} placeholder="Søg på navn, kundenr., CVR eller årsag"
             className="w-96 rounded-md border border-slate-300 px-3 py-1.5 text-sm" />

      {!liste ? <p className="text-slate-500">Henter …</p> : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr>
                <th className="px-3 py-2">Kunde</th><th className="px-3 py-2">Kundenr.</th><th className="px-3 py-2">CVR</th>
                <th className="px-3 py-2">Startdato</th><th className="px-3 py-2">Ophørsdato</th>
                <th className="px-3 py-2">Årsag</th><th className="px-3 py-2">Arkiveret af</th>
                <th className="px-3 py-2">Afregningsform</th>{admin && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {viste.map((k) => (
                <tr key={k.id} className="border-b border-slate-100">
                  <td className="px-3 py-2 font-medium text-slate-900"><Link href={`/kunder/kort/?id=${k.id}`} className="hover:text-brand-700 hover:underline">{k.navn}</Link></td>
                  <td className="px-3 py-2">{k.kundenummer}</td>
                  <td className="px-3 py-2">{k.cvr}</td>
                  <td className="px-3 py-2">{dato(k.startdato)}</td>
                  <td className="px-3 py-2">{dato(k.ophoersdato)}</td>
                  <td className="px-3 py-2">{k.aarsag ?? <span className="text-slate-400">Ikke angivet</span>}</td>
                  <td className="px-3 py-2">{k.arkiveret_af}</td>
                  <td className="px-3 py-2">{afregningTekst(k)}</td>
                  {admin && (
                    <td className="px-3 py-2 text-right">
                      <button onClick={() => genaktiver(k)}
                              className="rounded-md border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">
                        Genaktivér
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {viste.length === 0 && (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-slate-500">Ingen arkiverede kunder.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
