// Side til en del af hub'en, der ikke er bygget endnu. Indholdet aftales senere – her gættes ikke på noget.
export default function Pladsholder({ titel }: { titel: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold">{titel}</h1>
      <p className="mt-6 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
        Denne del af hub&apos;en er ikke bygget endnu. Indholdet aftales senere.
      </p>
    </div>
  );
}
