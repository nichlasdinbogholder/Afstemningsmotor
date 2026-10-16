"use client";

import { createContext, useContext, useState } from "react";

// Fagassistentens tilstand lever her – over hele hub'en (lagt ind i Ramme, som ligger i rodlayoutet).
// Derfor overlever samtalen navigation mellem siderne. Fra trin 3 hentes samtalen fra serveren ved
// indlæsning, så den også overlever en genindlæsning.

export type Besked = { id: number; rolle: "user" | "assistant"; tekst: string };

type Tilstand = {
  aaben: boolean;
  saetAaben: (aaben: boolean) => void;
  ulaest: boolean;
  beskeder: Besked[];
};

const NOEGLE = "fagassistent.aaben";
const AssistentKontekst = createContext<Tilstand | null>(null);

export function useAssistent(): Tilstand {
  const t = useContext(AssistentKontekst);
  if (!t) throw new Error("useAssistent skal bruges inden i AssistentProvider");
  return t;
}

export function AssistentProvider({ children }: { children: React.ReactNode }) {
  // Åben/lukket huskes i browseren. Kan den ikke læses (fx privat vindue), starter panelet lukket.
  // (Provideren vises først efter login-tjekket i browseren, så localStorage findes altid her.)
  const [aaben, setAaben] = useState(() => {
    try {
      return localStorage.getItem(NOEGLE) === "1";
    } catch {
      return false;
    }
  });
  const [ulaest, setUlaest] = useState(false);
  const [beskeder] = useState<Besked[]>([]);

  function saetAaben(ny: boolean) {
    setAaben(ny);
    if (ny) setUlaest(false);
    try {
      localStorage.setItem(NOEGLE, ny ? "1" : "0");
    } catch {}
  }

  return (
    <AssistentKontekst.Provider value={{ aaben, saetAaben, ulaest, beskeder }}>
      {children}
    </AssistentKontekst.Provider>
  );
}
