"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { Fragment, useCallback, useEffect, useState } from "react";
import { parseAnswer, type Block, type Inline } from "@/lib/answer";
import { generateCheatsheet, getCheatsheet, getMatter, listCheatsheets, type Cheatsheet, type MatterDetail, type ModelKey } from "@/lib/api";
import { useDateTime } from "@/lib/format";
import { ModelPicker, useModels } from "../workbench/ModelPicker";

/** ADR-028 (Brand.md 9.9): inside a matter, generate and list its key-points sheets. */
export function CheatsheetSection({ matterId, open }: { matterId: string; open: boolean }) {
  const t = useTranslations("cheatsheet");
  const locale = useLocale();
  const when = useDateTime();
  const models = useModels();
  const [model, setModel] = useState<ModelKey | null>(null);
  const [sheets, setSheets] = useState<Cheatsheet[] | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const load = useCallback(() => {
    listCheatsheets(matterId)
      .then(setSheets)
      .catch(() => setSheets([]));
  }, [matterId]);
  useEffect(load, [load]);

  const run = async () => {
    setFailed(false);
    setStage("collecting");
    try {
      await generateCheatsheet(matterId, { language: locale.startsWith("en") ? "en" : "es", ...(model ? { model_profile: model } : {}) }, setStage);
      load();
    } catch {
      setFailed(true);
    } finally {
      setStage(null);
    }
  };
  const [latest, ...older] = sheets ?? [];
  return (
    <section aria-labelledby="matter-sheet" className="sheet-section">
      <h3 id="matter-sheet">{t("section")}</h3>
      <p className="small muted">{t("intro")}</p>
      {open && (
        <>
          <ModelPicker info={models} value={model} onChange={setModel} compact />
          <p>
            <button type="button" className="button" onClick={() => void run()} disabled={stage !== null}>
              {t("generate")}
            </button>
          </p>
        </>
      )}
      <p className="small" role="status">
        {stage ? t(`stage.${stage === "generating" ? "generating" : "collecting"}`) : ""}
      </p>
      {failed && (
        <p className="form-status" role="alert">
          {t("error")}
        </p>
      )}
      {sheets && sheets.length === 0 && <p className="small muted">{t("none")}</p>}
      {latest && (
        <div className="sheet-latest">
          <h4>{t("latest")}</h4>
          <p className="small muted">
            {t("meta", { when: when(latest.created_at), name: latest.created_by_name ?? "—" })} · {t(`status.${latest.status}`)}
          </p>
          {latest.status === "answered" ? (
            <Link className="button button-quiet" href={`/asuntos/hoja?m=${matterId}&h=${latest.id}`}>
              {t("open")}
            </Link>
          ) : (
            <p className="refusal-note">{latest.refusal_reason === "retrieval.no_matter_sources" ? t("noSources") : t("refused")}</p>
          )}
        </div>
      )}
      {older.length > 0 && (
        <>
          <h4>{t("history")}</h4>
          <ul className="matter-items">
            {older.map((s) => (
              <li key={s.id}>
                {s.status === "answered" ? <Link href={`/asuntos/hoja?m=${matterId}&h=${s.id}`}>{t("meta", { when: when(s.created_at), name: s.created_by_name ?? "—" })}</Link> : t("meta", { when: when(s.created_at), name: s.created_by_name ?? "—" })}
                <span className="small muted"> · {t(`status.${s.status}`)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

type Numbers = Map<string, number>;

function SheetInline({ items, numbers }: { items: Inline[]; numbers: Numbers }) {
  return (
    <>
      {items.map((it, i) => {
        if (it.kind === "text") return <Fragment key={i}>{it.text}</Fragment>;
        if (it.kind === "bold") return <strong key={i}>{it.text}</strong>;
        if (it.kind === "mark") return null;
        return (
          <sup key={i} className="sheet-cite">
            [{it.ids.map((id) => numbers.get(id) ?? "?").join(", ")}]
          </sup>
        );
      })}
    </>
  );
}

/** The sheet's sections as they were validated: headings, then lines. A marked line (reasoning, no
 *  normative support) carries ◇ and its own style, never the look of a citation. */
function sections(blocks: Block[]): { heading: Inline[] | null; lines: Block[] }[] {
  const out: { heading: Inline[] | null; lines: Block[] }[] = [];
  for (const b of blocks) {
    if (b.kind === "heading") out.push({ heading: b.inline, lines: [] });
    else if (b.kind !== "gap") {
      if (out.length === 0) out.push({ heading: null, lines: [] });
      out[out.length - 1].lines.push(b);
    }
  }
  return out;
}

/** The printable page (`/asuntos/hoja?m=…&h=…`): letter size, one page, "Imprimir o guardar PDF". */
export function CheatsheetPrint() {
  const t = useTranslations("cheatsheet");
  const tm = useTranslations("workbench.model.name");
  const when = useDateTime();
  const [ids, setIds] = useState<{ m: string; h: string } | null>(null);
  const [sheet, setSheet] = useState<Cheatsheet | null>(null);
  const [matter, setMatter] = useState<MatterDetail | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const m = q.get("m") ?? "";
    const h = q.get("h") ?? "";
    const uuid = /^[0-9a-f-]{36}$/;
    if (!uuid.test(m) || !uuid.test(h)) {
      setMissing(true);
      return;
    }
    setIds({ m, h });
    Promise.all([getCheatsheet(m, h), getMatter(m)])
      .then(([s, d]) => {
        setSheet(s);
        setMatter(d);
      })
      .catch(() => setMissing(true));
  }, []);
  if (missing) return <p className="tool-page">{t("notFound")}</p>;
  if (!sheet || !matter || !ids) return <p className="tool-page muted">{t("loading")}</p>;
  if (sheet.status !== "answered" || !sheet.content) return <p className="tool-page">{t("refused")}</p>;
  const numbers: Numbers = new Map(sheet.citations.map((c, i) => [c.cite_id, i + 1]));
  const model = ["gemma", "qwen", "claude"].includes(sheet.model_profile) ? tm(sheet.model_profile) : sheet.model_profile;
  return (
    <div className="sheet-page">
      <p className="no-print sheet-actions">
        <button type="button" className="button" onClick={() => window.print()}>
          {t("print")}
        </button>
        <Link className="button button-quiet" href={`/asuntos?m=${ids.m}`}>
          {t("back")}
        </Link>
      </p>
      <article className="sheet" aria-labelledby="sheet-title">
        <header className="sheet-head">
          <p className="sheet-eyebrow">{t("title")}</p>
          <h1 id="sheet-title">{matter.matter.title}</h1>
          <p className="sheet-meta">
            {[matter.matter.client_ref, t("generated", { when: when(sheet.created_at), name: sheet.created_by_name ?? "—", model })].filter(Boolean).join(" · ")}
          </p>
        </header>
        <div className="sheet-body">
          {sections(parseAnswer(sheet.content)).map((s, i) => (
            <section key={i} className="sheet-block">
              {s.heading && (
                <h2>
                  <SheetInline items={s.heading} numbers={numbers} />
                </h2>
              )}
              <ul>
                {s.lines.map((b, j) =>
                  "inline" in b ? (
                    <li key={j} className={b.kind !== "heading" && b.marked ? "sheet-reasoning" : undefined}>
                      {b.kind !== "heading" && b.marked && <span aria-hidden="true">◇ </span>}
                      <SheetInline items={b.inline} numbers={numbers} />
                    </li>
                  ) : null,
                )}
              </ul>
            </section>
          ))}
        </div>
        <footer className="sheet-foot">
          <h2>{t("sources")}</h2>
          <ol>
            {sheet.citations.map((c) => (
              <li key={c.cite_id}>{c.citation_string}</li>
            ))}
          </ol>
          <p>{t("legend")}</p>
          <p>{t("disclaimer")}</p>
        </footer>
      </article>
    </div>
  );
}
