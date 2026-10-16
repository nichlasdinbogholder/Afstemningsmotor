"use client";

import { useEffect, useRef } from "react";
import { useAssistent } from "./Provider";

// Rund knap nederst til højre på alle sider; åbner et panel (400 × 600 px, hele skærmen under 640 px).
// Esc lukker. Fokus flytter ind i feltet ved åbning og tilbage til knappen ved lukning, og Tab bliver
// inden i panelet, mens det er åbent.
export default function Fagassistent() {
  const { aaben, saetAaben, ulaest, beskeder } = useAssistent();
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
        <button type="button" onClick={() => saetAaben(false)} aria-label="Luk fagassistenten"
                className="rounded-md p-1.5 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-white">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
        {beskeder.length === 0 && (
          <div className="rounded-lg bg-slate-50 p-3 text-slate-600">
            <p>Spørg om regler for moms, bogføring og skat. Assistenten forklarer rammen – det bindende svar
              giver den kundeansvarlige.</p>
            <p className="mt-2 text-xs text-slate-500">Assistenten er ikke koblet på endnu – det sker i næste trin.</p>
          </div>
        )}
      </div>

      <form onSubmit={(e) => e.preventDefault()} className="border-t border-slate-200 p-3">
        <textarea ref={felt} rows={2} aria-label="Skriv dit spørgsmål" placeholder="Skriv dit spørgsmål …"
                  className="w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-600 focus:outline-none" />
        <div className="mt-2 flex justify-end">
          <button type="submit" disabled title="Kommer i næste trin"
                  className="cursor-not-allowed rounded-md bg-brand-600/40 px-4 py-1.5 text-sm text-white">Send</button>
        </div>
      </form>
    </div>
  );
}
