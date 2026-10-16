import type { Alvor, Status } from "@/lib/api";
import { ALVOR, STATUS } from "@/lib/format";

const ALVOR_FARVE: Record<Alvor, string> = {
  high: "bg-red-100 text-red-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-slate-100 text-slate-700",
};

const STATUS_FARVE: Record<Status, string> = {
  open: "bg-blue-100 text-blue-800",
  accepted: "bg-emerald-100 text-emerald-800",
  resolved: "bg-emerald-100 text-emerald-800",
  ignored: "bg-slate-100 text-slate-600",
};

export function AlvorMaerke({ alvor }: { alvor: Alvor }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${ALVOR_FARVE[alvor]}`}>{ALVOR[alvor]}</span>;
}

export function StatusMaerke({ status }: { status: Status }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_FARVE[status]}`}>{STATUS[status]}</span>;
}

export function Tal({ n, farve }: { n: number; farve: string }) {
  return <span className={`inline-block min-w-8 rounded px-2 py-0.5 text-center text-xs font-semibold ${n ? farve : "text-slate-300"}`}>{n}</span>;
}
