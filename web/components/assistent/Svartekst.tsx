"use client";

import { Fragment } from "react";

// Svar vises som almindelig tekst. Kun **fed** og punktopstillinger ("- ") vises pænt – intet andet
// tolkes, så tekst fra modellen aldrig kan blive til HTML.
export default function Svartekst({ tekst }: { tekst: string }) {
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
