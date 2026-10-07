"use client";

import { useEffect, useState } from "react";
import { hent } from "@/lib/api";
import { beloeb, dato, KANAL, SPAERRE, tidspunkt } from "@/lib/format";

type Detalje = {
  fakturanummer: string; art: string; oprettet: string; forfald: string; beloeb: string; restbeloeb: string; ean: string | null;
  tidligere_rykkere: number;
  debitor: { nummer: string; navn: string; email: string | null; cvr: string | null; ean: string | null; adresse: string | null;
             postnr: string | null; by: string | null; erhverv: boolean; kanal: string | null; blokeret: boolean; note: string | null };
  betalinger: { dato: string; beloeb: string; kilde: string }[];
  rykkere: { id: number; nr: number; status: string; dato: string; sendt: string | null; gebyr: string; rente: string; kompensation: string }[];
  spaerrer: { aarsag: string; detaljer: Record<string, unknown> | null; tidspunkt: string }[];
  gebyrer: { type: string; paalagt: string; indbetalt: string; fakturerbar: string | null; afskrevet: string | null }[];
};

export default function FakturaPanel({ fakturaId, luk }: { fakturaId: number; luk: () => void }) {
  const [f, setF] = useState<Detalje | null>(null);
  useEffect(() => { hent<Detalje>(`/api/opkraevning/faktura/${fakturaId}`).then(setF); }, [fakturaId]);

  return (
    <aside className="sticky top-4 h-fit max-h-[calc(100vh-2rem)] w-[26rem] shrink-0 overflow-y-auto rounded-lg border border-slate-200 bg-white p-5 text-sm">
      <div className="mb-2 flex justify-between">
        <h2 className="font-semibold">Faktura {f?.fakturanummer}{f?.art === "credit_note" && " (kreditnota)"}</h2>
        <button onClick={luk} className="text-slate-400 hover:text-slate-900" aria-label="Luk">✕</button>
      </div>
      {!f ? <p className="text-slate-500">Henter …</p> : (
        <>
          <dl className="grid grid-cols-2 gap-y-1">
            <dt className="text-slate-500">Oprettet</dt><dd>{dato(f.oprettet)}</dd>
            <dt className="text-slate-500">Betalingsdag</dt><dd>{dato(f.forfald)}</dd>
            <dt className="text-slate-500">Beløb</dt><dd className="tabular-nums">{beloeb(f.beloeb)} kr.</dd>
            <dt className="text-slate-500">Restbeløb</dt><dd className="font-semibold tabular-nums">{beloeb(f.restbeloeb)} kr.</dd>
            {f.tidligere_rykkere > 0 && <><dt className="text-slate-500">Rykkere fra FarPay</dt><dd>{f.tidligere_rykkere}</dd></>}
          </dl>

          <Overskrift>Debitor</Overskrift>
          <p className="font-medium">{f.debitor.navn} <span className="font-normal text-slate-500">({f.debitor.nummer}, {f.debitor.erhverv ? "erhverv" : "privat"})</span></p>
          <p className="text-slate-600">{[f.debitor.adresse, [f.debitor.postnr, f.debitor.by].filter(Boolean).join(" ")].filter(Boolean).join(", ")}</p>
          <p className="text-slate-600">{f.debitor.email ?? <span className="text-red-700">ingen e-mail</span>}{f.debitor.cvr && ` · CVR ${f.debitor.cvr}`}{(f.ean || f.debitor.ean) && ` · EAN ${f.ean ?? f.debitor.ean}`}</p>
          <p className="text-slate-600">Kanal: {f.debitor.kanal ? KANAL[f.debitor.kanal] : "automatisk"}{f.debitor.blokeret && <span className="ml-2 rounded bg-red-100 px-1.5 text-xs font-semibold text-red-800">Blokeret for rykkere</span>}</p>
          {f.debitor.note && <p className="mt-1 rounded bg-amber-50 p-2 text-amber-900">{f.debitor.note}</p>}

          <Overskrift>Indbetalinger</Overskrift>
          {f.betalinger.length === 0 ? <p className="text-slate-400">Ingen</p> : f.betalinger.map((b, i) => (
            <p key={i} className="flex justify-between"><span>{dato(b.dato)} · {b.kilde}</span><span className="tabular-nums">{beloeb(b.beloeb)} kr.</span></p>
          ))}

          <Overskrift>Rykkere</Overskrift>
          {f.rykkere.length === 0 ? <p className="text-slate-400">Ingen</p> : f.rykkere.map((r) => (
            <p key={r.id} className="flex justify-between">
              <span>Nr. {r.nr} · {r.status === "sent" ? `sendt ${tidspunkt(r.sendt)}` : r.status === "queued" ? `i kø til ${dato(r.dato)}` : "annulleret"}</span>
              <span className="tabular-nums text-slate-600">{beloeb(r.gebyr)} + {beloeb(r.rente)}{Number(r.kompensation) > 0 && ` + ${beloeb(r.kompensation)}`}</span>
            </p>
          ))}

          <Overskrift>Gebyrer og renter</Overskrift>
          {f.gebyrer.length === 0 ? <p className="text-slate-400">Ingen</p> : f.gebyrer.map((g, i) => (
            <p key={i} className="flex justify-between"><span>{g.type}{g.afskrevet && ` (afskrevet: ${g.afskrevet})`}</span>
              <span className="tabular-nums">{beloeb(g.indbetalt)} / {beloeb(g.paalagt)} kr.</span></p>
          ))}

          <Overskrift>Hvorfor ikke rykket</Overskrift>
          {f.spaerrer.length === 0 ? <p className="text-slate-400">Ingen spærrer registreret</p> : f.spaerrer.slice(0, 10).map((s, i) => (
            <p key={i}><span className="text-slate-500">{tidspunkt(s.tidspunkt)}</span> · {SPAERRE[s.aarsag] ?? s.aarsag}</p>
          ))}
        </>
      )}
    </aside>
  );
}

function Overskrift({ children }: { children: React.ReactNode }) {
  return <h3 className="mt-5 mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{children}</h3>;
}
