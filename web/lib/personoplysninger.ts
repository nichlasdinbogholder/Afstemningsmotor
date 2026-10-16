// Advarsel før afsendelse i fagassistenten: ligner beskeden personoplysninger, bremser panelet brugeren.
// Det er en BREMSE, ikke en blokering – brugeren kan sende alligevel. Mønstrene er bevidst brede.

// Skrevet uden "lookbehind", så mønstrene også virker i ældre Safari på Mac og iPhone.
const MOENSTRE: RegExp[] = [
  /(^|\D)\d{6}[- ]?\d{4}(\D|$)/, // CPR-nummer: 6 cifre, evt. bindestreg eller mellemrum, 4 cifre
  /(^|\D)\d{4}[ -]\d{6,10}(\D|$)/, // kontonummer: reg.nr. (4 cifre) + konto (6–10 cifre)
  /\d{11,}/, // meget lange talrækker (fx kontonummer uden mellemrum)
];

export function lignerPersonoplysninger(tekst: string): boolean {
  return MOENSTRE.some((m) => m.test(tekst));
}

export const ADVARSEL = "Det ligner personoplysninger. Send dem ikke i chatten.";
