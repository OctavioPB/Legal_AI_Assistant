"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, chatStream, getJurisdictions, getMe, getPersonas, type ChatResult, type Persona, type User } from "@/lib/api";
import { resolve, type Jurisdiction, type Selection } from "@/lib/jurisdiction";
import { AnswerView } from "./AnswerView";
import { EmptyState } from "./EmptyState";
import { JurisdictionSelector } from "./JurisdictionSelector";
import { RefusalCard } from "./RefusalCard";
import { SourceViewer } from "./SourceViewer";

const MAX_QUERY = 4000;
const STORAGE_KEY = "legalai.selection.v1";
const WORKFLOWS = ["dossier_builder", "adversarial"] as const;

interface Saved {
  selection: Selection;
  persona: string;
  workflow: string;
}

// Per-viewer convenience only (never state that must persist): every access is guarded.
function load(): Saved | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}
function save(value: Saved) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    /* storage blocked: the page works without it */
  }
}

type View =
  | { kind: "idle" }
  | { kind: "running"; stage: string }
  | { kind: "result"; result: ChatResult }
  | { kind: "error"; code: string };

export function Workspace() {
  const t = useTranslations("workspace");
  const ts = useTranslations("session");
  const locale = useLocale();
  const [rows, setRows] = useState<Jurisdiction[] | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [failed, setFailed] = useState(false);
  const [selection, setSelection] = useState<Selection>({ country: "MX", state: null, municipality: null });
  const [persona, setPersona] = useState("general");
  const [workflow, setWorkflow] = useState<string>("dossier_builder");
  const [historical, setHistorical] = useState(false);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>({ kind: "idle" });
  const [open, setOpen] = useState<string | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    Promise.all([getJurisdictions(), getPersonas(), getMe()])
      .then(([j, p, me]) => {
        setRows(j);
        setPersonas(p);
        setUser(me);
        const saved = load();
        if (saved && resolve(j, saved.selection)) {
          setSelection(saved.selection);
          if (p.some((x) => x.code === saved.persona)) setPersona(saved.persona);
          if ((WORKFLOWS as readonly string[]).includes(saved.workflow)) setWorkflow(saved.workflow);
        }
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) window.location.assign("/login");
        else setFailed(true);
      });
  }, []);

  useEffect(() => {
    if (rows) save({ selection, persona, workflow });
  }, [rows, selection, persona, workflow]);

  const current = useMemo(() => (rows ? resolve(rows, selection) : null), [rows, selection]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!current || !query.trim() || view.kind === "running") return;
    setView({ kind: "running", stage: "retrieving" });
    try {
      const result = await chatStream(
        { query: query.trim(), jurisdiction_id: current.id, persona, workflow, language: locale === "en-US" ? "en" : "es", include_historical: historical },
        (stage) => setView({ kind: "running", stage }),
      );
      setView({ kind: "result", result });
      resultRef.current?.focus();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        window.location.assign("/login");
        return;
      }
      setView({ kind: "error", code: e instanceof ApiError ? e.code : "generic" });
    }
  }

  async function logout() {
    await fetch("/api/session", { method: "DELETE" });
    window.location.assign("/login");
  }

  if (failed) return <p className="form-status" role="alert">{t("catalogError")}</p>;
  if (!rows) return <p aria-busy="true">{t("loading")}</p>;

  const errorKey = view.kind === "error" ? (["rate_limited", "llm_unavailable"].includes(view.code) ? view.code : "generic") : null;
  return (
    <>
      <div className="session-bar">
        <span>{user ? ts("signedInAs", { name: user.display_name }) : null}</span>
        <button type="button" className="button button-quiet" onClick={logout}>
          {ts("logout")}
        </button>
      </div>
      <h1>{t("title")}</h1>
      <form className="workspace" onSubmit={submit}>
        <JurisdictionSelector rows={rows} value={selection} onChange={setSelection} />
        <fieldset className="controls">
          <legend>{t("persona.label")}</legend>
          <label className="field">
            <span>{t("persona.label")}</span>
            <select value={persona} onChange={(e) => setPersona(e.target.value)}>
              {personas.map((p) => (
                <option key={p.code} value={p.code}>
                  {t.has(`persona.${p.code}`) ? t(`persona.${p.code}`) : locale === "en-US" ? p.label_en : p.label_es}
                </option>
              ))}
            </select>
          </label>
          <div className="segmented" role="group" aria-label={t("workflow.label")}>
            {WORKFLOWS.map((w) => (
              <button key={w} type="button" aria-pressed={workflow === w} onClick={() => setWorkflow(w)}>
                {t(`workflow.${w}`)}
              </button>
            ))}
          </div>
          <label className="check">
            <input type="checkbox" checked={historical} onChange={(e) => setHistorical(e.target.checked)} />
            <span>{t("includeHistorical")}</span>
          </label>
        </fieldset>
        <div className="query">
          <label className="field">
            <span>{t("query.label")}</span>
            <textarea
              value={query}
              maxLength={MAX_QUERY}
              rows={4}
              placeholder={t("query.placeholder")}
              onChange={(e) => setQuery(e.target.value)}
              required
            />
          </label>
          <p className="count" aria-live="off">
            {t("query.count", { count: query.length, max: MAX_QUERY })}
          </p>
          <button className="button" type="submit" disabled={view.kind === "running" || !query.trim()}>
            {view.kind === "running" ? t("query.submitting") : t("query.submit")}
          </button>
        </div>
      </form>

      <div ref={resultRef} tabIndex={-1} className="result" aria-live="polite">
        {view.kind === "idle" && <EmptyState />}
        {view.kind === "running" && (
          <p className="stage" role="status">
            {t(`stage.${view.stage === "generating" ? "generating" : "retrieving"}`)}
          </p>
        )}
        {view.kind === "error" && errorKey && (
          <p className="form-status" role="alert">
            {t(`error.${errorKey}`)}
          </p>
        )}
        {view.kind === "result" && view.result.status === "answered" && view.result.answer && (
          <AnswerView text={view.result.answer} citations={view.result.citations} onOpen={setOpen} />
        )}
        {view.kind === "result" && view.result.status === "refused" && <RefusalCard reason={view.result.refusal_reason} />}
      </div>
      <SourceViewer citeId={open} onClose={() => setOpen(null)} />
    </>
  );
}
