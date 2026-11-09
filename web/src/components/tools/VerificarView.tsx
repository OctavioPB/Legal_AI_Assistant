"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState } from "react";
import {
  ApiError,
  deleteReport,
  FINDING_STATUSES,
  listReports,
  verifyPdf,
  verifyText,
  type Finding,
  type FindingStatus,
  type Report,
  type VerifyResult,
} from "@/lib/api";
import { useDateTime } from "@/lib/format";
import { SourceViewer } from "../SourceViewer";
import { MatterPicker, useInitialMatter, useMatters } from "../workbench/MatterPicker";

const MAX_TEXT = 400_000;
const MAX_PDF = 15 * 1024 * 1024;

// Each status has a shape + icon + label (Brand.md 9.2): never colour alone.
const ICON: Record<FindingStatus, string> = {
  vigente: "✓",
  derogado: "◷",
  no_vigente: "◷",
  inexistente: "⌕",
  fuera_del_corpus: "○",
  ley_no_identificada: "◇",
  ambigua: "◇",
};
const TONE: Record<FindingStatus, string> = {
  vigente: "ok",
  derogado: "warn",
  no_vigente: "warn",
  inexistente: "missing",
  fuera_del_corpus: "neutral",
  ley_no_identificada: "check",
  ambigua: "check",
};

/** Feature 2 (ADR-019, Brand.md 9.2): check every statute reference of a brief. The brief is
 *  processed in memory and never stored; only the report is kept. */
export function VerificarView() {
  const t = useTranslations("verify");
  const matters = useMatters();
  const when = useDateTime();
  const [matter, setMatter] = useInitialMatter();
  const ids = { title: useId(), text: useId(), file: useId(), mode: useId() };
  const [mode, setMode] = useState<"text" | "pdf">("text");
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ res: VerifyResult; text: string } | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [viewing, setViewing] = useState<string | null>(null);

  const focusResult = useRef(false);
  useEffect(() => {
    if (result && focusResult.current) {
      focusResult.current = false;
      document.getElementById("verify-result")?.focus();
    }
  }, [result]);
  const refresh = () => listReports().then(setReports).catch(() => setReports([]));
  useEffect(() => {
    void refresh();
  }, []);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    const name = title.trim() || (file?.name ?? t("untitled"));
    setBusy(true);
    setError(null);
    try {
      focusResult.current = true; // set before the result arrives: the effect focuses it once drawn
      if (mode === "text") {
        const res = await verifyText(name.slice(0, 200), brief, matter);
        setResult({ res, text: brief });
      } else if (file) {
        const res = await verifyPdf(name.slice(0, 200), file, matter);
        setResult({ res, text: res.text ?? "" });
      }
      await refresh();
    } catch (err) {
      focusResult.current = false;
      setError(err instanceof ApiError && err.status === 413 ? "tooLarge" : err instanceof ApiError && err.status === 422 ? "unreadable" : "generic");
    } finally {
      setBusy(false);
    }
  }

  const ready = mode === "text" ? brief.trim().length > 0 : Boolean(file);
  return (
    <div className="tool-page">
      <header className="tool-head">
        <h1>{t("title")}</h1>
        <p className="muted">{t("intro")}</p>
      </header>
      <form className="welcome verify-form" onSubmit={run}>
        <fieldset className="mode-switch">
          <legend>{t("mode")}</legend>
          {(["text", "pdf"] as const).map((m) => (
            <label key={m} className="check">
              <input type="radio" name={ids.mode} value={m} checked={mode === m} onChange={() => setMode(m)} />
              {t(`modes.${m}`)}
            </label>
          ))}
        </fieldset>
        <div className="field">
          <label htmlFor={ids.title}>{t("docTitle")}</label>
          <input id={ids.title} value={title} maxLength={200} placeholder={t("docTitlePlaceholder")} onChange={(e) => setTitle(e.target.value)} />
        </div>
        {mode === "text" ? (
          <div className="field">
            <label htmlFor={ids.text}>{t("text")}</label>
            <textarea id={ids.text} rows={10} value={brief} maxLength={MAX_TEXT} placeholder={t("textPlaceholder")} onChange={(e) => setBrief(e.target.value)} />
            <p className="small muted">
              {t("example")}{" "}
              <button type="button" className="button button-quiet" onClick={() => setBrief(t("exampleText"))}>
                {t("loadExample")}
              </button>
            </p>
          </div>
        ) : (
          <div className="field">
            <label htmlFor={ids.file}>{t("file")}</label>
            <input
              id={ids.file}
              type="file"
              accept="application/pdf,.pdf"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f && f.size <= MAX_PDF ? f : null);
                setError(f && f.size > MAX_PDF ? "tooLarge" : null);
              }}
            />
            <p className="small muted">{t("fileHelp")}</p>
          </div>
        )}
        <MatterPicker matters={matters} value={matter} onChange={setMatter} />
        <p className="small muted">{t("privacy")}</p>
        <button type="submit" className="button" disabled={busy || !ready}>
          {busy ? t("running") : t("run")}
        </button>
        {busy && (
          <p className="small muted" role="status">
            {mode === "pdf" ? t("runningPdf") : t("running")}
          </p>
        )}
        {error && (
          <p className="form-status" role="alert">
            {t(`errors.${error}`)}
          </p>
        )}
      </form>
      {result && <ResultView res={result.res} text={result.text} onOpen={setViewing} />}
      <section className="reports" aria-labelledby="verify-reports">
        <h2 id="verify-reports">{t("reports")}</h2>
        {reports.length === 0 ? (
          <p className="small muted">{t("noReports")}</p>
        ) : (
          <ul className="report-list">
            {reports.map((r) => (
              <li key={r.id}>
                <span className="report-title">{r.title}</span>
                <span className="small muted">
                  {" "}
                  · {when(r.created_at)} · {t("counts", { total: r.summary.total ?? 0, ok: r.summary.vigente ?? 0 })}
                </span>
                <button type="button" className="button button-quiet" onClick={() => setResult({ res: { report_id: r.id, title: r.title, summary: r.summary, findings: r.items, text: null, warnings: [] }, text: "" })}>
                  {t("openReport")}
                </button>
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={async () => {
                    await deleteReport(r.id).catch(() => undefined);
                    if (result?.res.report_id === r.id) setResult(null);
                    await refresh();
                  }}
                >
                  {t("deleteReport")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

function ResultView({ res, text, onOpen }: { res: VerifyResult; text: string; onOpen: (id: string) => void }) {
  const t = useTranslations("verify");
  const counts = FINDING_STATUSES.filter((s) => (res.summary[s] ?? 0) > 0);
  return (
    <section className="verify-result" aria-labelledby="verify-result" >
      <h2 id="verify-result" tabIndex={-1}>
        {t("resultTitle", { title: res.title })}
      </h2>
      {res.findings.length === 0 ? (
        <p>{t("none")}</p>
      ) : (
        <>
          <ul className="verify-summary" aria-label={t("summary")}>
            {counts.map((s) => (
              <li key={s} className={`finding-badge tone-${TONE[s]}`}>
                <span aria-hidden="true">{ICON[s]} </span>
                {t(`status.${s}`)}: {res.summary[s]}
              </li>
            ))}
          </ul>
          {res.warnings.length > 0 && <p className="small muted">{t("pdfWarnings")}</p>}
          <ol className="findings">
            {res.findings.map((f, i) => (
              <FindingRow key={`${f.ref}-${i}`} f={f} n={i + 1} text={text} onOpen={onOpen} />
            ))}
          </ol>
        </>
      )}
      <p className="small muted">{t("disclaimer")}</p>
    </section>
  );
}

function FindingRow({ f, n, text, onOpen }: { f: Finding; n: number; text: string; onOpen: (id: string) => void }) {
  const t = useTranslations("verify");
  const tr = useTranslations("reforms.change");
  const excerpt = f.span && text ? context(text, f.span) : null;
  return (
    <li className="finding">
      <p className="finding-head">
        <span className="finding-ref">{f.ref}</span>
        <span className={`finding-badge tone-${TONE[f.status]}`}>
          <span aria-hidden="true">{ICON[f.status]} </span>
          {t(`status.${f.status}`)}
        </span>
        {f.quote && (
          <span className={`finding-badge tone-${f.quote === "coincide" ? "ok" : f.quote === "corta" ? "neutral" : f.quote === "no_coincide" ? "missing" : "warn"}`}>
            {t(`quote.${f.quote}`)}
          </span>
        )}
      </p>
      <p className="small muted">{t(`statusHelp.${f.status}`)}</p>
      {f.inferred && <p className="small">{t("inferred")}</p>}
      {f.reform && (
        <p className="small">
          <span aria-hidden="true">◷ </span>
          {t("reform", { change: tr(f.reform.change), date: f.reform.published_on || "—" })}
        </p>
      )}
      {excerpt && (
        <blockquote className="finding-excerpt" aria-label={t("inBrief", { n })}>
          {excerpt.before}
          <mark>{excerpt.hit}</mark>
          {excerpt.after}
        </blockquote>
      )}
      {f.cite_ids.length > 0 && (
        <p className="finding-cites">
          {f.cite_ids.slice(0, 4).map((c) => (
            <button key={c} type="button" className="chip" title={c} onClick={() => onOpen(c)}>
              <span aria-hidden="true">✓ </span>
              {t("openSource")} <span className="cite-id">{c}</span>
            </button>
          ))}
        </p>
      )}
    </li>
  );
}

function context(text: string, [a, b]: [number, number]) {
  const start = Math.max(0, text.lastIndexOf("\n", a) + 1, a - 120);
  const end = Math.min(text.length, b + 160);
  return {
    before: (start > 0 ? "…" : "") + text.slice(start, a),
    hit: text.slice(a, b),
    after: text.slice(b, end) + (end < text.length ? "…" : ""),
  };
}
