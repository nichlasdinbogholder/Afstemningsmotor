// Låseikon ud for alt, der KUN kan ses eller rettes af administratorer (bygherren 26.10.2026).
// Selve adgangen afgøres altid på serveren – ikonet er kun en markering.
export default function AdminLaas({ className = "" }: { className?: string }) {
  return (
    <svg role="img" aria-label="Kun administratorer" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
         className={`inline-block h-3.5 w-3.5 shrink-0 align-[-2px] text-amber-600 ${className}`}>
      <title>Kun administratorer</title>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}
