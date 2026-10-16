import type { Metadata } from "next";
import Ramme from "@/components/Ramme";
import "./globals.css";

export const metadata: Metadata = {
  title: "Afstemningsmotor",
  description: "Afstemning for Din Bogholder",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="da" className="h-full antialiased">
      <body className="min-h-full bg-slate-50 text-slate-900">
        <Ramme>{children}</Ramme>
      </body>
    </html>
  );
}
