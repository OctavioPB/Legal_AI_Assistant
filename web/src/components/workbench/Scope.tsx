"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import type { Conversation, Persona } from "@/lib/api";
import { resolve, scopeChain, type Jurisdiction, type Selection } from "@/lib/jurisdiction";
import { JurisdictionSelector } from "../JurisdictionSelector";

export interface Scope {
  selection: Selection;
  persona: string;
  historical: boolean;
}

const STORAGE_KEY = "legalai.scope.v1";
export const DEFAULT_SCOPE: Scope = { selection: { country: "MX", state: null, municipality: null }, persona: "general", historical: false };

// Per-viewer convenience only (the scope that matters is stored with each conversation).
export function loadScope(rows: Jurisdiction[], personas: Persona[]): Scope {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const saved = raw ? (JSON.parse(raw) as Scope) : null;
    if (saved && resolve(rows, saved.selection) && personas.some((p) => p.code === saved.persona)) {
      return { selection: saved.selection, persona: saved.persona, historical: Boolean(saved.historical) };
    }
  } catch {
    /* storage blocked or corrupted: start from the default */
  }
  return DEFAULT_SCOPE;
}

function saveScope(scope: Scope) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(scope));
  } catch {
    /* storage blocked: the page works without it */
  }
}

export function personaLabel(t: (k: string) => string, has: (k: string) => boolean, personas: Persona[], code: string, locale: string): string {
  if (has(`persona.${code}`)) return t(`persona.${code}`);
  const p = personas.find((x) => x.code === code);
  return p ? (locale === "en-US" ? p.label_en : p.label_es) : code;
}

/** Scope of a NEW conversation: jurisdiction (closed catalog), profile and vigencia. Once the
 *  conversation exists it is fixed (ADR-016) and shown by `ScopeSummary`.
 *  Minimal configuration: it opens as ONE line with sensible defaults (or the user's last choice)
 *  and a "Cambiar" button; the selectors appear only when asked for. */
export function ScopePicker({
  rows,
  personas,
  value,
  onChange,
}: {
  rows: Jurisdiction[];
  personas: Persona[];
  value: Scope;
  onChange: (next: Scope) => void;
}) {
  const t = useTranslations("workspace");
  const tw = useTranslations("workbench.scope");
  const locale = useLocale();
  const panelId = useId();
  const personaHelp = useId();
  const [touched, setTouched] = useState(false);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (touched) saveScope(value);
  }, [touched, value]);
  const change = (next: Scope) => {
    setTouched(true);
    onChange(next);
  };
  const chain = scopeChain(rows, value.selection, locale).join(" + ");
  return (
    <div className="scope-picker">
      <div className="scope-line">
        <p className="scope-current">
          <span className="muted">{tw("current")}</span> <strong>{chain}</strong>
          {" · "}
          {personaLabel(t, t.has, personas, value.persona, locale)}
          {" · "}
          {value.historical ? tw("withHistorical") : tw("currentOnly")}
        </p>
        <button type="button" className="button button-quiet" aria-expanded={editing} aria-controls={panelId} onClick={() => setEditing((v) => !v)}>
          {editing ? tw("done") : tw("change")}
        </button>
      </div>
      <div id={panelId} hidden={!editing}>
        <JurisdictionSelector rows={rows} value={value.selection} onChange={(selection) => change({ ...value, selection })} />
        <fieldset className="controls">
          <legend>{t("persona.label")}</legend>
          <label className="field">
            <span>{t("persona.label")}</span>
            <select value={value.persona} aria-describedby={personaHelp} onChange={(e) => change({ ...value, persona: e.target.value })}>
              {personas.map((p) => (
                <option key={p.code} value={p.code}>
                  {personaLabel(t, t.has, personas, p.code, locale)}
                </option>
              ))}
            </select>
          </label>
          <p id={personaHelp} className="small muted">
            {tw("personaHelp")}
          </p>
          <label className="check">
            <input type="checkbox" checked={value.historical} onChange={(e) => change({ ...value, historical: e.target.checked })} />
            <span>{t("includeHistorical")}</span>
          </label>
          <p className="small muted">{tw("historicalHelp")}</p>
        </fieldset>
      </div>
    </div>
  );
}

/** The fixed scope of an existing conversation, read-only. */
export function ScopeSummary({ conversation, rows, personas }: { conversation: Conversation; rows: Jurisdiction[]; personas: Persona[] }) {
  const t = useTranslations("workspace");
  const tw = useTranslations("workbench");
  const locale = useLocale();
  const row = rows.find((r) => r.id === conversation.jurisdiction_id);
  const chain = row ? scopeChain(rows, { country: row.country, state: row.state_code, municipality: row.municipality }, locale).join(" + ") : "—";
  return (
    <p className="scope-summary">
      <span className="scope-chip">{chain}</span>
      <span className="scope-chip">{personaLabel(t, t.has, personas, conversation.persona, locale)}</span>
      {conversation.include_historical && <span className="scope-chip">{tw("historical")}</span>}
      <span className={conversation.model_profile === "claude" ? "scope-chip scope-chip-remote" : "scope-chip"}>
        {tw("model.fixed", { name: tw(`model.name.${conversation.model_profile ?? "gemma"}`) })}
        {conversation.model_profile === "claude" && ` · ${tw("model.fixedRemote")}`}
      </span>
      <span className="muted small">{tw("scopeFixed")}</span>
    </p>
  );
}
