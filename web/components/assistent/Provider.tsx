"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { hent, send } from "@/lib/api";

// Fagassistentens tilstand lever her – over hele hub'en (lagt ind i Ramme, som ligger i rodlayoutet).
// Derfor overlever samtalen navigation mellem siderne, og den hentes fra serveren ved indlæsning, så den
// også overlever en genindlæsning. Al logik (gemning, historik, kaldet til modellen) ligger på serveren.

export type Besked = {
  id: number | null; rolle: "user" | "assistant"; tekst: string; status: string; min_vurdering?: string | null;
  kilder?: { kb_id: number; title: string }[];
  opslag?: string[]; // det, Svend slog op i kundens regnskab (beskrevet af serveren)
};
type Samtale = { id: number; titel: string | null; kunde_id: number | null };
export type Kunde = { id: number; navn: string; kundenummer: string };

type Tilstand = {
  aaben: boolean;
  saetAaben: (aaben: boolean) => void;
  ulaest: boolean;
  slaaetTil: boolean | null;
  beskeder: Besked[];
  svarer: boolean;
  fejl: string | null;
  spoerg: (tekst: string) => Promise<void>;
  nySamtale: () => void;
  kunder: Kunde[];
  kundeId: number | null;
  vaelgKunde: (id: number | null) => void;
  vurder: (beskedId: number, vurdering: "up" | "down", kommentar?: string) => Promise<void>;
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
  const [slaaetTil, setSlaaetTil] = useState<boolean | null>(null);
  const [samtaleId, setSamtaleId] = useState<number | null>(null);
  // Samtalens kunde (null = "Generelt"). Kunderne i vælgeren er kun dem, man er ansvarlig for (serveren afgør).
  const [kundeId, setKundeId] = useState<number | null>(null);
  const [kunder, setKunder] = useState<Kunde[]>([]);
  const [beskeder, setBeskeder] = useState<Besked[]>([]);
  const [svarer, setSvarer] = useState(false);
  const [fejl, setFejl] = useState<string | null>(null);

  // Hent status og den seneste samtale fra serveren.
  useEffect(() => {
    hent<{ slaaet_til: boolean }>("/api/v1/assistant/status").then((s) => setSlaaetTil(s.slaaet_til)).catch(() => setSlaaetTil(false));
    hent<Kunde[]>("/api/v1/assistant/kunder").then(setKunder).catch(() => {});
    hent<Samtale[]>("/api/v1/assistant/conversations")
      .then(async (samtaler) => {
        if (samtaler.length === 0) return;
        const b = await hent<Besked[]>(`/api/v1/assistant/conversations/${samtaler[0].id}/messages`);
        setSamtaleId(samtaler[0].id);
        setKundeId(samtaler[0].kunde_id);
        setBeskeder(b);
      })
      .catch(() => {});
  }, []);

  function saetAaben(ny: boolean) {
    setAaben(ny);
    if (ny) setUlaest(false);
    try {
      localStorage.setItem(NOEGLE, ny ? "1" : "0");
    } catch {}
  }

  // Et kundeskift starter altid en NY samtale – en tråd må ikke blande to kunders tal.
  function vaelgKunde(id: number | null) {
    if (svarer || id === kundeId) return;
    setKundeId(id);
    setSamtaleId(null);
    setBeskeder([]);
    setFejl(null);
  }

  function nySamtale() {
    if (svarer) return;
    setSamtaleId(null);
    setBeskeder([]);
    setFejl(null);
  }

  // Tommel op/ned på et svar (gemmes på serveren – én gang pr. svar).
  async function vurder(beskedId: number, vurdering: "up" | "down", kommentar?: string) {
    await send(`/api/v1/assistant/messages/${beskedId}/feedback`, { vurdering, kommentar: kommentar || null });
    setBeskeder((b) => b.map((x) => (x.id === beskedId ? { ...x, min_vurdering: vurdering } : x)));
  }

  // Tilføj tekst til det svar, der er ved at blive skrevet (det sidste i listen).
  function tilfoej(tekst: string) {
    setBeskeder((b) => [...b.slice(0, -1), { ...b[b.length - 1], tekst: b[b.length - 1].tekst + tekst }]);
  }

  async function spoerg(tekst: string) {
    if (svarer || !tekst.trim()) return;
    setFejl(null);
    setSvarer(true);
    setBeskeder((b) => [...b, { id: null, rolle: "user", tekst: tekst.trim(), status: "complete" },
                         { id: null, rolle: "assistant", tekst: "", status: "streaming" }]);
    try {
      let id = samtaleId;
      if (id === null) {
        id = (await send<Samtale>("/api/v1/assistant/conversations", { kunde_id: kundeId })).id;
        setSamtaleId(id);
      }
      const r = await fetch(`/api/v1/assistant/conversations/${id}/messages`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Afstemning": "1" },
        body: JSON.stringify({ tekst }),
      });
      if (!r.ok || !r.body) {
        let besked = "Svend kunne ikke svare lige nu. Prøv igen om lidt.";
        try {
          const d = await r.json();
          if (typeof d.detail === "string") besked = d.detail;
        } catch {}
        throw new Error(besked);
      }
      // Svaret kommer som små stykker (Server-Sent Events): "data: {...}" adskilt af en tom linje.
      const laeser = r.body.getReader();
      const afkoder = new TextDecoder();
      let rest = "";
      let slutStatus: string | null = null;
      for (;;) {
        const { value, done } = await laeser.read();
        if (done) break;
        rest += afkoder.decode(value, { stream: true });
        const dele = rest.split("\n\n");
        rest = dele.pop() ?? "";
        for (const del of dele) {
          if (!del.startsWith("data: ")) continue;
          const h = JSON.parse(del.slice(6));
          if (h.type === "start") {
            setBeskeder((b) => [...b.slice(0, -1), { ...b[b.length - 1], kilder: h.kilder ?? [] }]);
          } else if (h.type === "tekst") tilfoej(h.tekst);
          else if (h.type === "slut") {
            slutStatus = h.status;
            setBeskeder((b) => [...b.slice(0, -1), { ...b[b.length - 1], id: h.besked_id, status: h.status, opslag: h.opslag ?? [] }]);
          } else if (h.type === "fejl") setFejl(h.besked);
        }
      }
      // Kom der intet svar, fjernes den tomme boble.
      if (slutStatus === null) setBeskeder((b) => (b[b.length - 1]?.tekst ? b : b.slice(0, -1)));
      if (!aabenNu()) setUlaest(true);
    } catch (e) {
      setBeskeder((b) => (b[b.length - 1]?.rolle === "assistant" && !b[b.length - 1].tekst ? b.slice(0, -1) : b));
      setFejl(e instanceof Error ? e.message : "Svend kunne ikke svare lige nu.");
    } finally {
      setSvarer(false);
    }
  }

  // Læser den aktuelle åben/lukket-tilstand (svaret kan blive færdigt, efter panelet er lukket).
  function aabenNu(): boolean {
    try {
      return localStorage.getItem(NOEGLE) === "1";
    } catch {
      return true;
    }
  }

  return (
    <AssistentKontekst.Provider value={{ aaben, saetAaben, ulaest, slaaetTil, beskeder, svarer, fejl, spoerg, nySamtale, vurder, kunder, kundeId, vaelgKunde }}>
      {children}
    </AssistentKontekst.Provider>
  );
}
