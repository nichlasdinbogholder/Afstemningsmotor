"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { useAssistent } from "./Provider";

// Svar vises som almindelig tekst. Kun **fed** og punktopstillinger ("- ") vises pænt – intet andet
// tolkes, så tekst fra modellen aldrig kan blive til HTML.
function Svartekst({ tekst }: { tekst: string }) {
  return (
    <>
      {tekst.split("\n").map((linje, i) => {
        const punkt = /^\s*[-*•] /.test(linje);
        const indhold = (punkt ? linje.replace(/^\s*[-*•] /, "") : linje).split("**").map((del, j) =>
          j % 2 === 1 ? <strong key={j}>{del}</strong> : <Fragment key={j}>{del}</Fragment>);
        if (punkt) return <div key={i} className="flex gap-2 pl-1"><span aria-hidden="true">•</span><span>{indhold}</span></div>;
        return linje.trim() === "" ? <div key={i} className="h-2" /> : <div key={i}>{indhold}</div>;
      })}
    </>
  );
}

// Rund knap nederst til højre på alle sider; åbner et panel (400 × 600 px, hele skærmen under 640 px).
// Esc lukker. Fokus flytter ind i feltet ved åbning og tilbage til knappen ved lukning, og Tab bliver
// inden i panelet, mens det er åbent.
export default function Fagassistent() {
  const { aaben, saetAaben, ulaest, slaaetTil, beskeder, svarer, fejl, spoerg, nySamtale } = useAssistent();
  const [tekst, setTekst] = useState("");
  const bund = useRef<HTMLDivElement>(null);
  const knap = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const felt = useRef<HTMLTextAreaElement>(null);
  const forrige = useRef(aaben);

  // Flyt kun fokus, når brugeren selv åbner/lukker – ikke når siden indlæses med panelet åbent.
  useEffect(() => {
    if (aaben && !forrige.current) felt.current?.focus();
    if (!aaben && forrige.current) knap.current?.focus();
    forrige.current = aaben;
  }, [aaben]);

  // Rul med ned, mens svaret skrives.
  useEffect(() => {
    bund.current?.scrollIntoView({ block: "end" });
  }, [beskeder, aaben]);

  function afsend() {
    if (!tekst.trim() || svarer || !slaaetTil) return;
    void spoerg(tekst);
    setTekst("");
  }

  function tastatur(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      saetAaben(false);
      return;
    }
    if (e.key !== "Tab" || !panel.current) return;
    const fokuserbare = Array.from(
      panel.current.querySelectorAll<HTMLElement>("button:not([disabled]), textarea, a[href], select, input"),
    );
    if (fokuserbare.length === 0) return;
    const foerste = fokuserbare[0];
    const sidste = fokuserbare[fokuserbare.length - 1];
    if (e.shiftKey && document.activeElement === foerste) {
      e.preventDefault();
      sidste.focus();
    } else if (!e.shiftKey && document.activeElement === sidste) {
      e.preventDefault();
      foerste.focus();
    }
  }

  if (!aaben)
    return (
      <button ref={knap} type="button" onClick={() => saetAaben(true)} aria-label="Åbn fagassistenten"
              className="fixed bottom-6 right-6 z-[1000] flex h-14 w-14 items-center justify-center rounded-full bg-brand-600 text-white shadow-lg hover:bg-brand-700 focus:outline-none focus-visible:ring-4 focus-visible:ring-brand-200">
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" />
        </svg>
        {ulaest && <span aria-label="Nyt svar" className="absolute right-1 top-1 h-3 w-3 rounded-full border-2 border-white bg-amber-400" />}
      </button>
    );

  return (
    <div ref={panel} role="dialog" aria-modal="true" aria-label="Fagassistent" onKeyDown={tastatur}
         className="fixed inset-0 z-[1000] flex flex-col overflow-hidden bg-white shadow-2xl sm:inset-auto sm:bottom-6 sm:right-6 sm:h-[600px] sm:w-[400px] sm:rounded-xl sm:border sm:border-slate-200">
      <div className="flex items-center justify-between bg-brand-600 px-4 py-3 text-white">
        <div>
          <p className="font-semibold">Fagassistent</p>
          <p className="text-xs text-brand-100">Moms, bogføring og skat for håndværkere</p>
        </div>
        <div className="flex items-center gap-1">
        <button type="button" onClick={nySamtale} disabled={svarer || beskeder.length === 0}
                className="whitespace-nowrap rounded-md px-2 py-1 text-xs hover:bg-brand-700 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-white">
          Ny samtale
        </button>
        <button type="button" onClick={() => saetAaben(false)} aria-label="Luk fagassistenten"
                className="rounded-md p-1.5 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-white">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm" aria-live="polite">
        {beskeder.length === 0 && (
          <div className="rounded-lg bg-slate-50 p-3 text-slate-600">
            <p>Spørg om regler for moms, bogføring og skat. Assistenten forklarer rammen – det bindende svar
              giver den kundeansvarlige.</p>
            {slaaetTil === false && <p className="mt-2 text-xs text-slate-500">Assistenten er ikke slået til endnu.</p>}
          </div>
        )}
        {beskeder.map((b, i) => b.rolle === "user" ? (
          <div key={i} className="ml-8 whitespace-pre-wrap rounded-lg bg-brand-50 px-3 py-2 text-slate-900">{b.tekst}</div>
        ) : (
          <div key={i} className="mr-4 rounded-lg border border-slate-200 bg-white px-3 py-2 leading-relaxed text-slate-800">
            {b.tekst ? <Svartekst tekst={b.tekst} /> : <span className="text-slate-400">Tænker …</span>}
            {b.status === "interrupted" && <p className="mt-1 text-xs text-slate-400">(Svaret blev afbrudt)</p>}
          </div>
        ))}
        {fejl && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-red-800">{fejl}</p>}
        <div ref={bund} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); afsend(); }} className="border-t border-slate-200 p-3">
        <textarea ref={felt} rows={2} aria-label="Skriv dit spørgsmål" placeholder="Skriv dit spørgsmål …"
                  value={tekst} onChange={(e) => setTekst(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); afsend(); } }}
                  className="w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-600 focus:outline-none" />
        <div className="mt-2 flex justify-end">
          <button type="submit" disabled={!tekst.trim() || svarer || !slaaetTil}
                  className="rounded-md bg-brand-600 px-4 py-1.5 text-sm text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-brand-600/40">
            {svarer ? "Svarer …" : "Send"}
          </button>
        </div>
      </form>
    </div>
  );
}
