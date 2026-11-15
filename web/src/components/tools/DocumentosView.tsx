"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError, deleteFile, FILE_KINDS, getFileText, listFiles, MAX_FILE_BYTES, uploadFile, type ClientFile, type FileKind, type FileUnit } from "@/lib/api";
import { useDateTime } from "@/lib/format";
import { countries, label, municipalitiesOf, resolve, select, statesOf, type Selection } from "@/lib/jurisdiction";
import { useCatalogs } from "@/lib/useWorkbench";
import { SourceViewer } from "../SourceViewer";
import { MatterPicker, useInitialMatter, useMatters } from "../workbench/MatterPicker";
import { DEFAULT_SCOPE, loadScope } from "../workbench/Scope";

const POLL_MS = 3000;
const ICON = { processing: "◷", ready: "✓", failed: "⌕" } as const;
const TONE = { processing: "neutral", ready: "ok", failed: "missing" } as const;
const KNOWN_ERRORS = ["no_text", "unreadable", "too_long", "embedder_unavailable", "generation_mismatch", "interrupted"];

/** ADR-025, Brand.md 9.7: the firm's own documents. The file is turned into text on this machine
 *  and discarded; what is listed here is what the conversations of its matter can cite. */
export function DocumentosView() {
  const t = useTranslations("files");
  const tj = useTranslations("workspace.jurisdiction");
  const tc = useTranslations("workspace.country");
  const locale = useLocale();
  const when = useDateTime();
  const { catalogs, failed } = useCatalogs();
  const matters = useMatters();
  const [matter, setMatter] = useInitialMatter();
  const ids = { file: useId(), title: useId(), kind: useId(), lang: useId(), country: useId(), state: useId(), muni: useId() };
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<FileKind>("contrato");
  const [language, setLanguage] = useState<"es" | "en">(locale === "en-US" ? "en" : "es");
  const [where, setWhere] = useState<Selection>(DEFAULT_SCOPE.selection);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<ClientFile[] | null>(null);
  const [announce, setAnnounce] = useState("");
  const [viewing, setViewing] = useState<string | null>(null);

  useEffect(() => {
    if (catalogs) setWhere(loadScope(catalogs.rows, catalogs.personas).selection);
  }, [catalogs]);

  const refresh = useCallback(async () => {
    try {
      setFiles(await listFiles());
    } catch {
      setFiles((prev) => prev ?? []);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  // while something is being processed, ask again every few seconds; say so once it ends
  const pending = (files ?? []).filter((f) => f.status === "processing").length;
  const before = useRef(0);
  useEffect(() => {
    if (before.current > 0 && pending === 0) setAnnounce(t("doneAnnounce"));
    before.current = pending;
    if (pending === 0) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [pending, refresh, t]);

  const rows = catalogs?.rows ?? [];
  const target = resolve(rows, where);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !file || !target) return;
    setBusy(true);
    setError(null);
    try {
      const name = (title.trim() || file.name.replace(/\.[A-Za-z0-9]{1,5}$/, "")).slice(0, 200);
      await uploadFile({ title: name, kind, language, jurisdiction_id: target.id, matter_id: matter }, file);
      setFile(null);
      setTitle("");
      if (input.current) input.current.value = "";
      setAnnounce(t("uploadedAnnounce", { title: name }));
      await refresh();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(status === 413 ? "tooLarge" : status === 415 ? "unsupported" : status === 429 ? "busy" : status === 404 ? "matter" : "generic");
    } finally {
      setBusy(false);
    }
  }

  const matterName = (id: string | null) => (id === null ? t("firmWide") : (matters.find((m) => m.id === id)?.title ?? t("aMatter")));
  return (
    <div className="tool-page">
      <header className="tool-head">
        <h1>{t("title")}</h1>
        <p className="muted">{t("intro")}</p>
      </header>
      <form className="welcome file-form" onSubmit={submit}>
        <h2>{t("uploadTitle")}</h2>
        <div className="field">
          <label htmlFor={ids.file}>{t("file")}</label>
          <input
            id={ids.file}
            ref={input}
            type="file"
            accept=".pdf,.docx,.txt,.md,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              const ok = f !== null && f.size <= MAX_FILE_BYTES;
              setFile(ok ? f : null);
              setError(f && !ok ? "tooLarge" : null);
              if (ok && !title.trim()) setTitle(f.name.replace(/\.[A-Za-z0-9]{1,5}$/, "").slice(0, 200));
            }}
          />
          <p className="small muted">{t("fileHelp")}</p>
        </div>
        <div className="field">
          <label htmlFor={ids.title}>{t("docTitle")}</label>
          <input id={ids.title} value={title} maxLength={200} placeholder={t("docTitlePlaceholder")} onChange={(e) => setTitle(e.target.value)} />
          <p className="small muted">{t("docTitleHelp")}</p>
        </div>
        <div className="file-row">
          <div className="field">
            <label htmlFor={ids.kind}>{t("kind")}</label>
            <select id={ids.kind} value={kind} onChange={(e) => setKind(e.target.value as FileKind)}>
              {FILE_KINDS.map((k) => (
                <option key={k} value={k}>
                  {t(`kinds.${k}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={ids.lang}>{t("language")}</label>
            <select id={ids.lang} value={language} onChange={(e) => setLanguage(e.target.value as "es" | "en")}>
              <option value="es">{t("languages.es")}</option>
              <option value="en">{t("languages.en")}</option>
            </select>
          </div>
        </div>
        <MatterPicker matters={matters} value={matter} onChange={setMatter} />
        <p className="small muted">{matter ? t("visibleMatter") : t("visibleFirm")}</p>
        <fieldset className="controls">
          <legend>{t("where")}</legend>
          <div className="field">
            <label htmlFor={ids.country}>{tj("country")}</label>
            <select id={ids.country} value={where.country} onChange={(e) => setWhere(select(rows, where, { country: e.target.value as Selection["country"] }))}>
              {countries(rows).map((c) => (
                <option key={c} value={c}>
                  {tc(c)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={ids.state}>{tj("state")}</label>
            <select id={ids.state} value={where.state ?? ""} onChange={(e) => setWhere(select(rows, where, { state: e.target.value || null }))}>
              <option value="">{t("allStates")}</option>
              {statesOf(rows, where.country).map((s) => (
                <option key={s.state_code} value={s.state_code ?? ""}>
                  {label(s, locale)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={ids.muni}>{tj("municipality")}</label>
            <select
              id={ids.muni}
              value={where.municipality ?? ""}
              disabled={!where.state || municipalitiesOf(rows, where.country, where.state).length === 0}
              onChange={(e) => setWhere(select(rows, where, { municipality: e.target.value || null }))}
            >
              <option value="">{tj("noMunicipality")}</option>
              {municipalitiesOf(rows, where.country, where.state).map((m) => (
                <option key={m.municipality} value={m.municipality ?? ""}>
                  {m.municipality}
                </option>
              ))}
            </select>
          </div>
          <p className="small muted">{where.state ? t("whereHelpNarrow") : t("whereHelpAll")}</p>
        </fieldset>
        <p className="small muted">{t("privacy")}</p>
        <button type="submit" className="button" disabled={busy || !file || !target}>
          {busy ? t("uploading") : t("upload")}
        </button>
        {(error || failed) && (
          <p className="form-status" role="alert">
            {t(`errors.${error ?? "generic"}`)}
          </p>
        )}
      </form>
      <p className="sr-only" role="status">
        {announce}
      </p>
      <section className="reports" aria-labelledby="files-list">
        <h2 id="files-list">{t("listTitle")}</h2>
        {files === null ? (
          <p className="small muted">{t("loading")}</p>
        ) : files.length === 0 ? (
          <p className="small muted">{t("empty")}</p>
        ) : (
          <ul className="file-list">
            {files.map((f) => (
              <FileRow key={f.id} f={f} place={matterName(f.matter_id)} when={when(f.created_at)} onOpen={setViewing} onChanged={refresh} />
            ))}
          </ul>
        )}
      </section>
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

function FileRow({ f, place, when, onOpen, onChanged }: { f: ClientFile; place: string; when: string; onOpen: (id: string) => void; onChanged: () => Promise<void> }) {
  const t = useTranslations("files");
  const [confirm, setConfirm] = useState(false);
  const [units, setUnits] = useState<FileUnit[] | null>(null);
  const [open, setOpen] = useState(false);
  const reason = f.error_key && KNOWN_ERRORS.includes(f.error_key) ? f.error_key : "internal";
  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && units === null) setUnits(await getFileText(f.id).catch(() => []));
  }
  return (
    <li className="file-item">
      <p className="file-head">
        <span className="file-title">{f.title}</span>
        <span className={`finding-badge tone-${TONE[f.status]}`}>
          <span aria-hidden="true">{ICON[f.status]} </span>
          {f.status === "ready" ? t("status.ready", { count: f.chunk_count ?? 0 }) : t(`status.${f.status}`)}
        </span>
      </p>
      <p className="small muted">
        {t(`kinds.${f.kind}`)} · {place} · {when} · {t("by", { name: f.uploaded_by_name })}
      </p>
      {f.status === "failed" && <p className="small">{t(`failed.${reason}`)}</p>}
      {f.status === "processing" && <p className="small muted">{t("processingHelp")}</p>}
      <div className="bar-actions">
        {f.status === "ready" && (
          <>
            <button type="button" className="button button-quiet" aria-expanded={open} onClick={() => void toggle()}>
              {open ? t("hideText") : t("showText")}
            </button>
            <Link className="button button-quiet" href={f.matter_id ? `/conversar?matter=${f.matter_id}` : "/conversar"}>
              {t("ask")}
            </Link>
          </>
        )}
        {f.can_delete &&
          f.status !== "processing" &&
          (confirm ? (
            <span className="bar-confirm" role="group" aria-label={t("confirmDelete")}>
              <span>{t("confirmDelete")}</span>
              <button
                type="button"
                className="button"
                onClick={async () => {
                  await deleteFile(f.id).catch(() => undefined);
                  await onChanged();
                }}
              >
                {t("delete")}
              </button>
              <button type="button" className="button button-quiet" onClick={() => setConfirm(false)}>
                {t("cancel")}
              </button>
            </span>
          ) : (
            <button type="button" className="button button-quiet" onClick={() => setConfirm(true)}>
              {t("delete")}
            </button>
          ))}
      </div>
      {open && (
        <div className="file-text">
          {units === null ? (
            <p className="small muted">{t("loading")}</p>
          ) : units.length === 0 ? (
            <p className="small muted">{t("noUnits")}</p>
          ) : (
            <>
              <p className="small muted">{t("textHelp")}</p>
              <ol className="file-units">
                {units.map((u) => (
                  <li key={u.cite_id}>
                    <button type="button" className="source-open" onClick={() => onOpen(u.cite_id)}>
                      {u.citation_string}
                    </button>
                    <p className="file-unit-text">{u.text}</p>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      )}
    </li>
  );
}
