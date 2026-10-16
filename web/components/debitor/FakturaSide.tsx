"use client";

import { useEffect, useState } from "react";
import { hent } from "@/lib/api";
import { adresselinjer, beloeb, BETALINGSSTATUS, dato, KANAL, SENDSTATUS, SPAERRE, tidspunkt } from "@/lib/format";

type Adresse = { navn?: string | null; adresse: string | null; postnr: string | null; by: string | null } | null;
type Linje = { nr: number; vare?: string | null; beskrivelse: string | null; antal: string | null; enhed?: string | null;
               pris: string | null; rabat?: string | null; beloeb: string | null };
type Detalje = {
  fakturanummer: string; art: string; oprettet: string; forfald: string; beloeb: string; restbeloeb: string; ean: string | null;
  valuta: string; tidligere_rykkere: number; betalingsstatus: string; kanal: string | null; sendstatus: string | null;
  kunde: { navn: string; cvr: string | null; adresse: string | null; postnr: string | null; by: string | null };
  hoved: { ordrenummer: number | null; oevrig_ref: string | null; netto: string | null; moms: string | null;
           modtager: Adresse; levering: Adresse; overskrift: string | null; tekst: string | null };
  linjer: Linje[] | null;
  betalingsnoegle: string | null;
  log: { tid: string; tekst: string; aarsag?: string; af?: string | null }[];
  debitor: { nummer: string; navn: string; email: string | null; cvr: string | null; ean: string | null; adresse: string | null;
             postnr: string | null; by: string | null; erhverv: boolean; kanal: string | null; blokeret: boolean; note: string | null };
  betalinger: { dato: string; beloeb: string; kilde: string }[];
  rykkere: { id: number; nr: number; status: string; dato: string; sendt: string | null; gebyr: string; rente: string; kompensation: string }[];
  spaerrer: { aarsag: string; detaljer: Record<string, unknown> | null; tidspunkt: string }[];
  gebyrer: { type: string; paalagt: string; indbetalt: string; fakturerbar: string | null; afskrevet: string | null }[];
};

export default function FakturaSide({ fakturaId, tilbage }: { fakturaId: number; tilbage: () => void }) {
  const [f, setF] = useState<Detalje | null>(null);
  const [visLog, setVisLog] = useState(false);
  useEffect(() => { hent<Detalje>(`/api/opkraevning/faktura/${fakturaId}`).then(setF); }, [fakturaId]);

  if (!f) return <p className="text-slate-500">Henter faktura …</p>;
  const status = BETALINGSSTATUS[f.betalingsstatus] ?? { tekst: f.betalingsstatus, farve: "bg-slate-300" };
  const send = f.sendstatus ? SENDSTATUS[f.sendstatus] : null;
  const modtager = f.hoved.modtager ?? { navn: f.debitor.navn, adresse: f.debitor.adresse, postnr: f.debitor.postnr, by: f.debitor.by };
  const levering = adresselinjer(f.hoved.levering);
  const kreditnota = f.art === "credit_note";

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex items-center justify-between">
        <button onClick={tilbage} className="rounded-md border border-slate-300 bg-white px-4 py-1.5 text-sm hover:bg-slate-50">← Tilbage</button>
        <div className="flex gap-2">
          <button disabled title="Kommer med udsendelsen (trin 4)" className="cursor-not-allowed rounded-md bg-sky-700/40 px-4 py-1.5 text-sm text-white">Send e-mail</button>
          <button disabled title="Kommer med udsendelsen (trin 4)" className="cursor-not-allowed rounded-md bg-sky-700/40 px-4 py-1.5 text-sm text-white">PDF</button>
          <button onClick={() => setVisLog((v) => !v)} className={`rounded-md px-4 py-1.5 text-sm text-white ${visLog ? "bg-sky-900" : "bg-sky-700 hover:bg-sky-800"}`}>☰ Log</button>
        </div>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-4 rounded-lg border border-sky-200 bg-sky-50 px-6 py-4 text-center text-sm sm:grid-cols-5">
        <Felt navn="Betalingsdag">{dato(f.forfald)}</Felt>
        <Felt navn="Beløb">{beloeb(f.beloeb)}</Felt>
        <Felt navn="Restbeløb">{beloeb(f.restbeloeb)}</Felt>
        <Felt navn="Betalingsstatus"><span className={`inline-block w-28 rounded px-2 py-0.5 text-xs font-semibold ${status.farve}`}>{status.tekst}</span></Felt>
        <Felt navn="Sendstatus">
          {send ? <span className={`inline-block w-28 rounded px-2 py-0.5 text-xs font-semibold ${send.farve}`}>{send.tekst}</span>
                : <span className="inline-block w-28 rounded bg-slate-300 px-2 py-0.5 text-xs font-semibold text-white">Ikke sendt</span>}
        </Felt>
      </div>

      {visLog && (
        <div className="mb-5 rounded-lg border border-slate-200 bg-white p-5 text-sm">
          <h3 className="mb-2 font-semibold">Log</h3>
          {f.log.map((l, i) => (
            <p key={i} className="border-b border-slate-100 py-1 last:border-0">
              <span className="inline-block w-36 text-slate-500">{tidspunkt(l.tid)}</span>
              {l.tekst}{l.aarsag && `: ${SPAERRE[l.aarsag] ?? l.aarsag}`}{l.af && <span className="text-slate-500"> – {l.af}</span>}
            </p>
          ))}
        </div>
      )}

      {/* Fakturaen, som debitoren har modtaget den */}
      <div className="mb-6 rounded-lg border border-slate-200 bg-white px-12 py-10 shadow-sm">
        <div className="flex flex-wrap justify-between gap-8">
          <div>
            <p className="mb-4 text-3xl uppercase tracking-wide text-slate-900">{f.kunde.navn}</p>
            <p className="text-lg leading-snug">{modtager.navn ?? f.debitor.navn}</p>
            {adresselinjer(modtager).map((l) => <p key={l} className="text-lg leading-snug">{l}</p>)}
          </div>
          <dl className="grid min-w-72 grid-cols-[auto_1fr] gap-x-6 text-sm">
            <dt className="text-xl font-semibold">{kreditnota ? "Kreditnota" : "Faktura"}</dt><dd className="text-right text-xl font-semibold">{f.fakturanummer}</dd>
            <dt className="font-semibold">Dato</dt><dd className="text-right">{dato(f.oprettet)}</dd>
            {f.kunde.cvr && <><dt className="font-semibold">CVR-nr.</dt><dd className="text-right">{f.kunde.cvr}</dd></>}
            <dt className="font-semibold">Kundenr.</dt><dd className="text-right">{f.debitor.nummer}</dd>
            <dt className="font-semibold">Betalingsdato</dt><dd className="text-right">{dato(f.forfald)}</dd>
            {f.hoved.ordrenummer && <><dt className="font-semibold">Ordrenr.</dt><dd className="text-right">{f.hoved.ordrenummer}</dd></>}
            {f.hoved.oevrig_ref && <><dt className="font-semibold">Øvrig ref.</dt><dd className="text-right">{f.hoved.oevrig_ref}</dd></>}
            {levering.length > 0 && <><dt className="font-semibold">Leveringsadresse</dt><dd className="text-right">{levering.map((l) => <span key={l} className="block">{l}</span>)}</dd></>}
            {f.ean && <><dt className="font-semibold">EAN</dt><dd className="text-right">{f.ean}</dd></>}
            {f.betalingsnoegle && <><dt className="col-span-2 mt-3 font-semibold">Betalingsnøgle</dt>
              <dd className="col-span-2 font-mono text-[13px]">{f.betalingsnoegle}</dd></>}
          </dl>
        </div>

        {(f.hoved.overskrift || f.hoved.tekst) && (
          <div className="mt-8 text-sm">
            {f.hoved.overskrift && <p className="font-semibold">{f.hoved.overskrift}</p>}
            {f.hoved.tekst && <p className="whitespace-pre-line text-slate-700">{f.hoved.tekst}</p>}
          </div>
        )}

        <table className="mt-8 w-full border border-slate-300 text-sm">
          <thead className="border-b border-slate-300 text-left text-xs font-semibold uppercase">
            <tr><th className="border-r border-slate-300 px-3 py-2">Vare</th><th className="border-r border-slate-300 px-3 py-2">Beskrivelse</th>
              <th className="border-r border-slate-300 px-3 py-2 text-right">Antal</th><th className="border-r border-slate-300 px-3 py-2">Enhed</th>
              <th className="border-r border-slate-300 px-3 py-2 text-right">Pris</th><th className="px-3 py-2 text-right">Beløb ({f.valuta})</th></tr>
          </thead>
          <tbody>
            {f.linjer === null ? (
              <tr><td colSpan={6} className="px-3 py-4 text-center text-slate-500">Linjerne er ikke hentet endnu – de kommer med næste opdatering fra regnskabet.</td></tr>
            ) : f.linjer.map((l) => (
              <tr key={l.nr} className="border-b border-slate-200">
                <td className="border-r border-slate-300 px-3 py-2">{l.vare}</td>
                <td className="border-r border-slate-300 px-3 py-2">{l.beskrivelse}{l.rabat && Number(l.rabat) > 0 && <span className="text-slate-500"> (rabat {beloeb(l.rabat)} %)</span>}</td>
                <td className="border-r border-slate-300 px-3 py-2 text-right tabular-nums">{l.antal && new Intl.NumberFormat("da-DK").format(Number(l.antal))}</td>
                <td className="border-r border-slate-300 px-3 py-2">{l.enhed}</td>
                <td className="border-r border-slate-300 px-3 py-2 text-right tabular-nums">{beloeb(l.pris)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{beloeb(l.beloeb)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <dl className="ml-auto mt-4 grid w-72 grid-cols-2 gap-y-1 text-sm">
          {f.hoved.netto && <><dt>Subtotal</dt><dd className="text-right tabular-nums">{beloeb(f.hoved.netto)}</dd></>}
          {f.hoved.moms && <><dt>Moms</dt><dd className="text-right tabular-nums">{beloeb(f.hoved.moms)}</dd></>}
          <dt className="border-t border-slate-300 pt-1 font-semibold">I alt DKK</dt>
          <dd className="border-t border-slate-300 pt-1 text-right font-semibold tabular-nums">{beloeb(f.beloeb)}</dd>
        </dl>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Kort titel="Debitor">
          <p className="font-medium">{f.debitor.navn} <span className="font-normal text-slate-500">({f.debitor.nummer}, {f.debitor.erhverv ? "erhverv" : "privat"})</span></p>
          <p className="text-slate-600">{f.debitor.email ?? <span className="text-red-700">ingen e-mail</span>}{f.debitor.cvr && ` · CVR ${f.debitor.cvr}`}</p>
          <p className="text-slate-600">Kanal: {f.kanal ? KANAL[f.kanal] : <span className="font-medium text-red-700">Ingen kanal</span>}
            {f.debitor.blokeret && <span className="ml-2 rounded bg-red-100 px-1.5 text-xs font-semibold text-red-800">Blokeret for rykkere</span>}</p>
          {f.debitor.note && <p className="mt-1 rounded bg-amber-50 p-2 text-amber-900">{f.debitor.note}</p>}
        </Kort>
        <Kort titel="Indbetalinger">
          {f.betalinger.length === 0 ? <p className="text-slate-400">Ingen</p> : f.betalinger.map((b, i) => (
            <p key={i} className="flex justify-between"><span>{dato(b.dato)} · {b.kilde}</span><span className="tabular-nums">{beloeb(b.beloeb)} kr.</span></p>
          ))}
        </Kort>
        <Kort titel={`Rykkere${f.tidligere_rykkere ? ` (heraf ${f.tidligere_rykkere} fra FarPay)` : ""}`}>
          {f.rykkere.length === 0 ? <p className="text-slate-400">Ingen</p> : f.rykkere.map((r) => (
            <p key={r.id} className="flex justify-between">
              <span>{r.nr === 0 ? "Venlig påmindelse" : `Nr. ${r.nr}`} · {r.status === "sent" ? `sendt ${tidspunkt(r.sendt)}` : r.status === "queued" ? `i kø til ${dato(r.dato)}` : "annulleret"}</span>
              <span className="tabular-nums text-slate-600">{beloeb(r.gebyr)} + {beloeb(r.rente)}{Number(r.kompensation) > 0 && ` + ${beloeb(r.kompensation)}`}</span>
            </p>
          ))}
        </Kort>
        <Kort titel="Gebyrer og renter (indbetalt / pålagt)">
          {f.gebyrer.length === 0 ? <p className="text-slate-400">Ingen</p> : f.gebyrer.map((g, i) => (
            <p key={i} className="flex justify-between"><span>{g.type}{g.afskrevet && ` (afskrevet: ${g.afskrevet})`}</span>
              <span className="tabular-nums">{beloeb(g.indbetalt)} / {beloeb(g.paalagt)} kr.</span></p>
          ))}
        </Kort>
        <Kort titel="Hvorfor ikke rykket">
          {f.spaerrer.length === 0 ? <p className="text-slate-400">Ingen spærrer registreret</p> : f.spaerrer.slice(0, 10).map((s, i) => (
            <p key={i}><span className="text-slate-500">{tidspunkt(s.tidspunkt)}</span> · {SPAERRE[s.aarsag] ?? s.aarsag}</p>
          ))}
        </Kort>
      </div>
    </div>
  );
}

function Felt({ navn, children }: { navn: string; children: React.ReactNode }) {
  return <div><p className="font-semibold text-sky-800">{navn}</p><div className="mt-1 text-slate-700 tabular-nums">{children}</div></div>;
}

function Kort({ titel, children }: { titel: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{titel}</h3>
      {children}
    </section>
  );
}
