import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// På serveren bygges siderne som statiske filer (out/), som Caddy udleverer direkte – der kører
// ikke et Next.js-program i drift. Caddy sender /api, /login, /logout og /auth videre til Python-delen.
// Under udvikling (npm run dev) gør Next.js det selv, så login-cookien virker på samme adresse.
const API = process.env.API_URL ?? "http://localhost:8000";

// Tailwind behandles af Turbopack (som projektet blev oprettet med).
const turbopack: NextConfig["turbopack"] = {
  rules: { "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" } },
};

export default function config(phase: string): NextConfig {
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    return {
      turbopack,
      async rewrites() {
        return ["/api/:sti*", "/login", "/logout", "/auth/:sti*", "/dev-login"].map((source) => ({
          source,
          destination: `${API}${source}`,
        }));
      },
    };
  }
  return { turbopack, output: "export", trailingSlash: true, images: { unoptimized: true } };
}
