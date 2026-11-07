"use client";

import { useLocale, useTranslations } from "next-intl";
import {
  countries,
  label,
  municipalitiesOf,
  resolve,
  scopeChain,
  select,
  statesOf,
  type Jurisdiction,
  type Selection,
} from "@/lib/jurisdiction";

/** Country -> state -> municipality, built ONLY from the closed catalog (Brand.md 4): there is no
 *  free-text field, a lower level is disabled until its parent is chosen, and changing a parent
 *  resets the children. The stacked scope is always shown. */
export function JurisdictionSelector({
  rows,
  value,
  onChange,
}: {
  rows: Jurisdiction[];
  value: Selection;
  onChange: (next: Selection) => void;
}) {
  const t = useTranslations("workspace.jurisdiction");
  const tc = useTranslations("workspace.country");
  const locale = useLocale();
  const states = statesOf(rows, value.country);
  const munis = municipalitiesOf(rows, value.country, value.state);
  const chain = scopeChain(rows, value, locale).join(" + ");
  const current = resolve(rows, value);

  return (
    <fieldset className="controls">
      <legend>{t("legend")}</legend>
      <label className="field">
        <span>{t("country")}</span>
        <select value={value.country} onChange={(e) => onChange(select(rows, value, { country: e.target.value as Selection["country"] }))}>
          {countries(rows).map((c) => (
            <option key={c} value={c}>
              {tc(c)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>{t("state")}</span>
        <select value={value.state ?? ""} onChange={(e) => onChange(select(rows, value, { state: e.target.value || null }))}>
          <option value="">{t("federalOnly")}</option>
          {states.map((s) => (
            <option key={s.state_code} value={s.state_code ?? ""}>
              {label(s, locale)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>{t("municipality")}</span>
        <select
          value={value.municipality ?? ""}
          disabled={!value.state || munis.length === 0}
          onChange={(e) => onChange(select(rows, value, { municipality: e.target.value || null }))}
        >
          <option value="">{t("noMunicipality")}</option>
          {munis.map((m) => (
            <option key={m.municipality} value={m.municipality ?? ""}>
              {m.municipality}
            </option>
          ))}
        </select>
      </label>
      <p className="scope" data-jurisdiction-id={current?.id}>
        <strong>{t("scope", { chain })}</strong>
        <br />
        <small>{t("scopeNote")}</small>
      </p>
    </fieldset>
  );
}
