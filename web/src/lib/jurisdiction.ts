// Jurisdiction selection from the CLOSED catalog served by the API. The selects offer only catalog
// rows, so an invalid scope cannot be expressed from the UI (Brand.md section 4).

export interface Jurisdiction {
  id: number;
  country: "MX" | "US";
  level: "federal" | "state" | "municipal";
  state_code: string | null;
  municipality: string | null;
  name_es: string;
  name_en: string;
}

export interface Selection {
  country: "MX" | "US";
  state: string | null; // state_code
  municipality: string | null;
}

export const countries = (rows: Jurisdiction[]): ("MX" | "US")[] =>
  [...new Set(rows.map((r) => r.country))].sort() as ("MX" | "US")[];

export const statesOf = (rows: Jurisdiction[], country: string): Jurisdiction[] =>
  rows.filter((r) => r.country === country && r.level === "state");

export const municipalitiesOf = (rows: Jurisdiction[], country: string, state: string | null): Jurisdiction[] =>
  state ? rows.filter((r) => r.country === country && r.level === "municipal" && r.state_code === state) : [];

/** The catalog row a selection resolves to: municipal, else state, else federal. */
export function resolve(rows: Jurisdiction[], sel: Selection): Jurisdiction | null {
  const find = (level: Jurisdiction["level"], state: string | null, muni: string | null) =>
    rows.find((r) => r.country === sel.country && r.level === level && r.state_code === state && r.municipality === muni) ?? null;
  if (sel.state && sel.municipality) return find("municipal", sel.state, sel.municipality);
  if (sel.state) return find("state", sel.state, null);
  return find("federal", null, null);
}

/** Changing a level resets the levels below it (a municipality of another state is impossible). */
export function select(rows: Jurisdiction[], sel: Selection, change: Partial<Selection>): Selection {
  const next: Selection = { ...sel, ...change };
  if ("country" in change && change.country !== sel.country) return { country: next.country, state: null, municipality: null };
  if ("state" in change && change.state !== sel.state) next.municipality = null;
  if (next.state && !statesOf(rows, next.country).some((s) => s.state_code === next.state)) return { country: next.country, state: null, municipality: null };
  if (next.municipality && !municipalitiesOf(rows, next.country, next.state).some((m) => m.municipality === next.municipality)) next.municipality = null;
  return next;
}

export const label = (j: Jurisdiction, locale: string): string => (locale === "en-US" ? j.name_en : j.name_es);

/** Stacked scope, e.g. "Nuevo León + Monterrey": federal is always implied (jurisdictions.md). */
export function scopeChain(rows: Jurisdiction[], sel: Selection, locale: string): string[] {
  const out: string[] = [];
  const fed = resolve(rows, { country: sel.country, state: null, municipality: null });
  if (fed) out.push(label(fed, locale));
  if (sel.state) {
    const st = resolve(rows, { country: sel.country, state: sel.state, municipality: null });
    if (st) out.push(label(st, locale));
  }
  if (sel.state && sel.municipality) out.push(sel.municipality);
  return out;
}
