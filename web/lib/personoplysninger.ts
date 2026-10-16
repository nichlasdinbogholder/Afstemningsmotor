// Advarsel før afsendelse i fagassistenten: ligner beskeden personoplysninger, bremser panelet brugeren.
// Det er en BREMSE, ikke en blokering – brugeren kan sende alligevel. Mønstrene er bevidst brede.

const MOENSTRE: RegExp[] = [
  /(?<!\d)\d{6}-?\d{4}(?!\d)/, // CPR-nummer: 6 cifre, evt. bindestreg, 4 cifre
  /(?<!\d)\d{4}[ -]\d{6,10}(?!\d)/, // kontonummer: reg.nr. (4 cifre) + konto (6–10 cifre)
  /\d{11,}/, // meget lange talrækker (fx kontonummer uden mellemrum)
];

export function lignerPersonoplysninger(tekst: string): boolean {
  return MOENSTRE.some((m) => m.test(tekst));
}

export const ADVARSEL = "Det ligner personoplysninger. Send dem ikke i chatten.";
