"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useMig } from "@/components/Ramme";
import AdminLaas from "@/components/AdminLaas";
import { hent, send } from "@/lib/api";
import { type KartotekKunde, type Valg } from "@/lib/kartotek";

const AFREGNING = { abonnement: "Abonnement", timeafregning: "Timeafregning", intern: "Intern" };
const STATUS = { emne: "Emne", onboarding: "Onboarding", aktiv: "Aktiv", pause: "Pause", opsagt: "Arkiveret" };

// Kun dansk skrivemåde: 1500 · 1.500 · 1.500,50 – "1500.5" afvises, så et beløb aldrig gættes.
function danskTal(v: string): string | null {
  const t = v.trim();
  if (!/^\d{1,3}(\.\d{3})*(,\d{1,2})?$|^\d+(,\d{1,2})?$/.test(t)) return null;
  return t.replace(/\./g, "").replace(",", ".");
}

// Filtrene (trin 4). Standard: mine kunder.
const STANDARD_FILTRE = { mine: true, ansvarlig_id: "", afregning: "", system: "", status: "",
  doede_forbindelser: false, mangler_hvidvask: false };

function Valgfelt({ titel, vaerdi, skift, muligheder }: {
  titel: string; vaerdi: string; skift: (v: string) => void; muligheder: [string, string][];
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-slate-500">{titel}</span>
      <select value={vaerdi} onChange={(e) => skift(e.target.value)}
              className={`rounded-md border px-2 py-1.5 ${vaerdi ? "border-brand-600 bg-brand-50" : "border-slate-300"}`}>
        {muligheder.map(([v, tekst]) => <option key={v} value={v}>{tekst}</option>)}
      </select>
    </label>
  );
}

export default function Side() {
  const mig = useMig();
  const admin = mig?.rolle === "admin";
  const [liste, setListe] = useState<KartotekKunde[] | null>(null);
  const [valg, setValg] = useState<Valg | null>(null);
  const [soeg, setSoeg] = useState("");
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const [arkiverer, setArkiverer] = useState<number | null>(null);
  const [visOpret, setVisOpret] = useState(false);
  // Selve filtreringen og sorteringen sker på serveren.
  const [filtre, setFiltre] = useState(STANDARD_FILTRE);
  const hentListe = useCallback(() => {
    const q = new URLSearchParams();
    for (const [n, v] of Object.entries(filtre)) if (v) q.set(n, String(v));
    hent<KartotekKunde[]>(`/api/kundekartotek?${q}`).then(setListe);
  }, [filtre]);
  useEffect(() => { hentListe(); hent<Valg>("/api/kundekartotek/valg").then(setValg); }, [hentListe]);

  async function udfoer(handling: () => Promise<unknown>, ok: string): Promise<boolean> {
    try { await handling(); setBesked({ tekst: ok }); hentListe(); return true; }
    catch (e) { setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true }); return false; }
  }

  const s = soeg.trim().toLowerCase();
  const viste = (liste ?? []).filter((k) => !s || [k.navn, k.kundenummer, k.cvr ?? "", k.ansvarlig ?? ""]
    .some((v) => v.toLowerCase().includes(s)));

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Kundeoversigt</h1>
        </div>
        {admin && !visOpret && (
          <button onClick={() => setVisOpret(true)}
                  className="shrink-0 rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            <AdminLaas className="mr-1.5 text-white" />Opret kunde
          </button>
        )}
      </div>

      {admin && visOpret && valg && (
        <NyKunde valg={valg} luk={() => setVisOpret(false)}
                 gem={(d) => udfoer(() => send("/api/kundekartotek", d), `${d.navn} er oprettet`)} />
      )}

      {besked && <p className={`rounded-md px-3 py-2 text-sm ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}

      <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <input type="search" value={soeg} onChange={(e) => setSoeg(e.target.value)}
               placeholder="Søg på navn, kundenr., CVR eller ansvarlig"
               className="w-full rounded-md border border-slate-300 px-3 py-2 text-base" />
        <div className="grid grid-cols-4 gap-3">
          <Valgfelt titel="Ansvarlig" vaerdi={filtre.ansvarlig_id} skift={(v) => setFiltre({ ...filtre, ansvarlig_id: v })}
                    muligheder={[["", "Alle"], ...(valg?.medarbejdere ?? []).map((m) => [String(m.id), m.navn] as [string, string])]} />
          <Valgfelt titel="Afregning" vaerdi={filtre.afregning} skift={(v) => setFiltre({ ...filtre, afregning: v })}
                    muligheder={[["", "Alle"], ["abonnement", "Abonnement"], ["timeafregning", "Timeafregning"], ["intern", "Intern"]]} />
          <Valgfelt titel="System" vaerdi={filtre.system} skift={(v) => setFiltre({ ...filtre, system: v })}
                    muligheder={[["", "Alle"], ...(valg?.systemer ?? []).map((x) => [x.kode, x.navn] as [string, string])]} />
          <Valgfelt titel="Status" vaerdi={filtre.status} skift={(v) => setFiltre({ ...filtre, status: v })}
                    muligheder={[["", "Alle"], ["onboarding", "Onboarding"], ["aktiv", "Aktiv"], ["pause", "Pause"]]} />
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          <span className="mr-1 text-xs font-medium text-slate-500">Vis kun:</span>
          {([["mine", "Mine kunder", "Kunder, hvor du er ansvarlig eller assistent"],
             ["doede_forbindelser", "Døde forbindelser", "Forbindelsen til regnskabet er afvist eller fejler"],
             ["mangler_hvidvask", "Mangler hvidvask", "Ingen risikovurdering efter hvidvaskloven"]] as const).map(([n, tekst, forklaring]) => (
            <button key={n} type="button" title={forklaring} aria-pressed={filtre[n]}
                    onClick={() => setFiltre({ ...filtre, [n]: !filtre[n] })}
                    className={`rounded-full border px-3 py-1 ${filtre[n]
                      ? "border-brand-600 bg-brand-600 text-white"
                      : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
              {filtre[n] ? "✓ " : ""}{tekst}
            </button>
          ))}
          <span className="ml-auto text-slate-500">{liste ? `${viste.length} ${viste.length === 1 ? "kunde" : "kunder"}` : ""}</span>
          {(soeg || filtre.ansvarlig_id || filtre.afregning || filtre.system || filtre.status || !filtre.mine
            || filtre.doede_forbindelser || filtre.mangler_hvidvask) && (
            <button type="button" onClick={() => { setSoeg(""); setFiltre(STANDARD_FILTRE); }}
                    className="text-brand-700 hover:underline">Nulstil</button>
          )}
        </div>
      </div>

      {!liste ? <p className="text-slate-500">Henter …</p> : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr>
                <th className="px-3 py-2">Kunde</th><th className="px-3 py-2">Kundenr.</th><th className="px-3 py-2">CVR</th>
                <th className="px-3 py-2">Ansvarlig</th>
                <th className="px-3 py-2">Afregning</th><th className="px-3 py-2">System</th>
                <th className="px-3 py-2" title="Andel af bankposteringer af typen finans med bilag – bygges i et senere trin">Bilagsdækning</th>
                <th className="px-3 py-2">Status</th>
                {admin && <th className="px-3 py-2" title="Intern kunde: kun administratorer kan spørge Svend om den">Kun admin hos Svend <AdminLaas /></th>}
                {admin && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {viste.map((k) => (
                <Fragment key={k.id}>
                  <tr className="border-b border-slate-100">
                    <td className="px-3 py-2">
                      <Link href={`/kunder/kort/?id=${k.id}`} className="font-medium text-slate-900 hover:text-brand-700 hover:underline">{k.navn}</Link>
                    </td>
                    <td className="px-3 py-2">{k.kundenummer}</td>
                    <td className="px-3 py-2">{k.cvr}</td>
                    <td className="px-3 py-2">
                      {k.ansvarlig ?? <span className="text-slate-400">Ingen</span>}
                      <div className="text-xs text-slate-500">Assistent: {k.assistent ?? "ingen"}</div>
                    </td>
                    <td className="px-3 py-2">
                      {/* Kun afregningsformen – ikke prisen (bygherren 26.10.2026). */}
                      {k.afregning ? AFREGNING[k.afregning] : <span className="text-slate-400">Ikke sat</span>}
                    </td>
                    <td className="px-3 py-2">
                      {k.system}
                      <div className={`text-xs ${k.forbindelse === "afvist" || k.forbindelse === "fejlet" ? "font-semibold text-red-700" : k.forbindelse === "aktiv" ? "text-emerald-700" : "text-slate-500"}`}>
                        {k.forbindelse_tekst}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-400">kommer senere</td>
                    <td className="px-3 py-2">{STATUS[k.status]}</td>
                    {admin && (
                      <td className="px-3 py-2 text-center">
                        <input type="checkbox" checked={k.kun_admin} aria-label={`Kun admin hos Svend for ${k.navn}`}
                               onChange={(e) => udfoer(() => send(`/api/kundekartotek/${k.id}/kun-admin`, { kun_admin: e.target.checked }),
                                 e.target.checked ? `Kun administratorer kan nu spørge Svend om ${k.navn}`
                                                  : `${k.navn} kan nu bruges hos Svend af den ansvarlige`)} />
                      </td>
                    )}
                    {admin && (
                      <td className="px-3 py-2 text-right">
                        <button onClick={() => setArkiverer(arkiverer === k.id ? null : k.id)}
                                className="rounded-md border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">
                          <AdminLaas className="mr-1" />Arkivér
                        </button>
                      </td>
                    )}
                  </tr>
                  {admin && arkiverer === k.id && valg && (
                    <tr className="border-b border-slate-100 bg-slate-50">
                      <td colSpan={10} className="px-3 py-3">
                        <Arkivering kunde={k} valg={valg} luk={() => setArkiverer(null)}
                                    gem={(d) => udfoer(() => send(`/api/kundekartotek/${k.id}/arkiver`, d),
                                                       `${k.navn} er arkiveret`)} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {viste.length === 0 && (
                <tr><td colSpan={10} className="px-3 py-6 text-center text-slate-500">Ingen kunder fundet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type NyData = { navn: string; kundenummer: string; cvr: string | null; system: string; ansvarlig_id: number | null;
  startdato: string; afregning: string; abonnementspris: string | null; status: string };

function NyKunde({ valg, gem, luk }: { valg: Valg; gem: (d: NyData) => Promise<boolean>; luk: () => void }) {
  const [navn, setNavn] = useState("");
  const [kundenummer, setKundenummer] = useState("");
  const [cvr, setCvr] = useState("");
  const [system, setSystem] = useState(valg.systemer[0]?.kode ?? "");
  const [ansvarlig, setAnsvarlig] = useState("");
  const [startdato, setStartdato] = useState("");
  const [afregning, setAfregning] = useState("");
  const [status, setStatus] = useState("aktiv");
  const [pris, setPris] = useState("");
  const [fejl, setFejl] = useState<string | null>(null);

  async function indsend(e: React.FormEvent) {
    e.preventDefault();
    if (!startdato) return setFejl("Vælg startdatoen for samarbejdet");
    if (!afregning) return setFejl("Vælg afregningsform");
    let abonnementspris: string | null = null;
    if (afregning === "abonnement") {
      abonnementspris = danskTal(pris);
      if (abonnementspris === null) return setFejl("Skriv abonnementsprisen som fx 1.500 eller 1.500,50");
    }
    setFejl(null);
    const ok = await gem({ navn, kundenummer, cvr: cvr.trim() || null, system,
      ansvarlig_id: ansvarlig ? Number(ansvarlig) : null, startdato, afregning, abonnementspris, status });
    if (ok) luk();
  }

  const felt = "mt-1 block w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm";
  return (
    <form onSubmit={indsend} className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="font-semibold">Opret kunde <AdminLaas /></h2>
      <div className="mt-3 grid grid-cols-4 gap-4 text-sm">
        <label className="col-span-2">Navn<input required value={navn} onChange={(e) => setNavn(e.target.value)} className={felt} /></label>
        <label>Kundenummer<input required value={kundenummer} onChange={(e) => setKundenummer(e.target.value)} className={felt} /></label>
        <label>CVR<input value={cvr} onChange={(e) => setCvr(e.target.value)} inputMode="numeric" className={felt} /></label>
        <label>Regnskabssystem
          <select value={system} onChange={(e) => setSystem(e.target.value)} className={felt}>
            {valg.systemer.map((s) => <option key={s.kode} value={s.kode}>{s.navn}</option>)}
          </select>
        </label>
        <label>Ansvarlig medarbejder
          <select value={ansvarlig} onChange={(e) => setAnsvarlig(e.target.value)} className={felt}>
            <option value="">Ingen endnu</option>
            {valg.medarbejdere.map((m) => <option key={m.id} value={m.id}>{m.navn}</option>)}
          </select>
        </label>
        <label>Startdato for samarbejdet
          <input type="date" required value={startdato} onChange={(e) => setStartdato(e.target.value)} className={felt} />
        </label>
        <label>Status
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={felt}>
            <option value="aktiv">Aktiv</option>
            <option value="onboarding">Onboarding</option>
          </select>
        </label>
        <label>Afregningsform
          <select required value={afregning} onChange={(e) => setAfregning(e.target.value)} className={felt}>
            <option value="">Vælg …</option>
            <option value="abonnement">Abonnement</option>
            <option value="timeafregning">Timeafregning</option>
            <option value="intern">Intern – vores eget firma</option>
          </select>
        </label>
        {afregning === "abonnement" && (
          <label>Abonnementspris pr. måned (kr. ekskl. moms)
            <input value={pris} onChange={(e) => setPris(e.target.value)} placeholder="fx 1.500" className={felt} />
          </label>
        )}
      </div>
      <p className="mt-3 text-xs text-slate-500">
        Afregningsformen gælder fra startdatoen. Intern = vores eget firma: timerne faktureres aldrig og tæller ikke i
        omsætningen, men tæller i løn og arbejdstid – og kunden bliver automatisk &quot;Kun admin hos Svend&quot;. Kundens adgang til e-conomic lægges ind på serveren med
        kommandoen gem_token (koden indtastes skjult).
      </p>
      {fejl && <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{fejl}</p>}
      <div className="mt-4 flex gap-2">
        <button type="submit" className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">Opret</button>
        <button type="button" onClick={luk} className="rounded-md border border-slate-300 px-4 py-2 text-sm hover:bg-slate-50">Annullér</button>
      </div>
    </form>
  );
}

function Arkivering({ kunde, valg, gem, luk }: {
  kunde: KartotekKunde; valg: Valg; gem: (d: { aarsag: string; ophoersdato: string }) => Promise<boolean>; luk: () => void;
}) {
  const [aarsag, setAarsag] = useState("");
  const [ophoersdato, setOphoersdato] = useState("");
  const [fejl, setFejl] = useState<string | null>(null);

  async function indsend(e: React.FormEvent) {
    e.preventDefault();
    if (!aarsag) return setFejl("Vælg årsagen til, at kunden arkiveres");
    if (!ophoersdato) return setFejl("Vælg datoen for samarbejdets ophør");
    setFejl(null);
    if (await gem({ aarsag, ophoersdato })) luk();
  }

  return (
    <form onSubmit={indsend} className="flex flex-wrap items-end gap-4 text-sm">
      <p className="w-full text-slate-700">
        Arkivér <span className="font-medium">{kunde.navn}</span>. Kunden stoppes helt (ingen hentning, ingen rykkere,
        kan ikke vælges i timeregistreringen eller hos Svend). En administrator kan genaktivere kunden igen.
      </p>
      <label>Årsag
        <select value={aarsag} onChange={(e) => { setAarsag(e.target.value); setFejl(null); }}
                className="mt-1 block w-64 rounded-md border border-slate-300 px-2.5 py-1.5">
          <option value="">Vælg årsag …</option>
          {valg.aarsager.map((a) => <option key={a.kode} value={a.kode}>{a.navn}</option>)}
        </select>
      </label>
      <label>Dato for samarbejdets ophør
        <input type="date" value={ophoersdato} min={kunde.startdato ?? undefined} onChange={(e) => setOphoersdato(e.target.value)}
               className="mt-1 block rounded-md border border-slate-300 px-2.5 py-1.5" />
      </label>
      <button type="submit" className="rounded-md bg-red-600 px-4 py-2 font-medium text-white hover:bg-red-700">Arkivér kunden</button>
      <button type="button" onClick={luk} className="rounded-md border border-slate-300 px-4 py-2 hover:bg-slate-50">Annullér</button>
      {fejl && <p className="w-full rounded-md bg-red-50 px-3 py-2 text-red-800">{fejl}</p>}
    </form>
  );
}
