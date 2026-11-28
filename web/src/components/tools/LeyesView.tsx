"use client";

import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ApiError,
  findArticle,
  getHistory,
  getNode,
  getToc,
  listLaws,
  type Law,
  type LawVersion,
  type NodeView,
  type TocNode,
} from "@/lib/api";
import { diffWords } from "@/lib/diff";
import { SaveToMatter } from "./SaveToMatter";

function readParams(): { law: string | null; path: string | null } {
  const q = new URLSearchParams(window.location.search);
  return { law: q.get("ley"), path: q.get("p") };
}

function writeParams(law: string | null, path: string | null) {
  const q = new URLSearchParams();
  if (law) q.set("ley", law);
  if (path) q.set("p", path);
  const s = q.toString();
  const next = s ? `${window.location.pathname}?${s}` : window.location.pathname;
  if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", next);
}

/** Feature 1 (Brand.md 9.1): browse a loaded law by its structure, jump to an article by number,
 *  read the stored text exactly as ingested and compare it with earlier versions. No model. */
export function LeyesView() {
  const t = useTranslations("laws");
  const locale = useLocale();
  const [laws, setLaws] = useState<Law[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [law, setLaw] = useState<string | null>(null);
  const [toc, setToc] = useState<TocNode[] | null>(null);
  const [node, setNode] = useState<NodeView | null>(null);
  const [missing, setMissing] = useState(false);
  const ids = { law: useId(), find: useId() };
  const [ref, setRef] = useState("");
  const focusNext = useRef(false); // move the focus to the article once it is on screen

  useEffect(() => {
    listLaws()
      .then((l) => {
        setLaws(l);
        const p = readParams();
        const first = p.law && l.some((x) => x.official_id === p.law) ? p.law : (l[0]?.official_id ?? null);
        setLaw(first);
        if (first && p.path && p.law === first) void openNode(first, p.path, false);
      })
      .catch(() => setFailed(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!law) return;
    setToc(null);
    getToc(law)
      .then(setToc)
      .catch(() => setToc([]));
  }, [law]);

  const openNode = useCallback(async (id: string, path: string, focus = true) => {
    setMissing(false);
    try {
      const n = await getNode(id, path);
      focusNext.current = focus;
      setNode(n);
      writeParams(id, path);
    } catch {
      setMissing(true);
    }
  }, []);

  useEffect(() => {
    if (node && focusNext.current) {
      focusNext.current = false;
      document.getElementById("law-node-title")?.focus();
    }
  }, [node]);

  const current = laws?.find((l) => l.official_id === law) ?? null;
  return (
    <div className="tool-page">
      <header className="tool-head">
        <h1>{t("title")}</h1>
        <p className="muted">{t("intro")}</p>
      </header>
      {failed && (
        <p className="form-status" role="alert">
          {t("loadError")}
        </p>
      )}
      {laws && laws.length === 0 && <p>{t("empty")}</p>}
      {laws && laws.length > 0 && (
        <div className="laws-layout">
          <nav className="laws-nav" aria-label={t("structure")}>
            <div className="field">
              <label htmlFor={ids.law}>{t("law")}</label>
              <select
                id={ids.law}
                value={law ?? ""}
                onChange={(e) => {
                  setLaw(e.target.value);
                  setNode(null);
                  writeParams(e.target.value, null);
                }}
              >
                {laws.map((l) => (
                  <option key={l.official_id} value={l.official_id}>
                    {l.title} · {locale === "en-US" ? l.jurisdiction_en : l.jurisdiction_es}
                  </option>
                ))}
              </select>
            </div>
            {current && (
              <p className="small muted">
                {t("version", { version: current.version_label ?? "—" })}
                {current.last_reform && <> · {t("lastReform", { date: current.last_reform })}</>}
              </p>
            )}
            <form
              className="laws-find"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!law || !ref.trim()) return;
                try {
                  const hit = await findArticle(law, ref.trim());
                  await openNode(law, hit.path);
                } catch (err) {
                  setMissing(err instanceof ApiError && err.status === 404);
                }
              }}
            >
              <div className="field">
                <label htmlFor={ids.find}>{t("find")}</label>
                <input id={ids.find} value={ref} maxLength={40} inputMode="text" placeholder={t("findPlaceholder")} onChange={(e) => setRef(e.target.value)} />
              </div>
              <button type="submit" className="button">
                {t("go")}
              </button>
            </form>
            {missing && (
              <p className="small" role="status">
                {t("notFound")}
              </p>
            )}
            <h2 className="eyebrow">{t("structure")}</h2>
            {!toc && <p className="small muted">{t("loading")}</p>}
            {toc && <Toc nodes={toc} active={node?.path ?? null} onOpen={(p) => law && void openNode(law, p)} />}
          </nav>
          <section className="laws-main">
            {!node && <p className="muted">{t("pick")}</p>}
            {node && law && <Article law={law} node={node} onOpen={(p) => void openNode(law, p)} />}
          </section>
        </div>
      )}
    </div>
  );
}

function Toc({ nodes, active, onOpen }: { nodes: TocNode[]; active: string | null; onOpen: (path: string) => void }) {
  return (
    <ul className="toc">
      {nodes.map((n) => {
        const leaf = n.node_type === "articulo" || n.node_type === "transitorio";
        if (leaf) {
          return (
            <li key={n.path}>
              <button type="button" className="toc-leaf" aria-current={active === n.path ? "true" : undefined} onClick={() => onOpen(n.path)}>
                <span className="toc-label">{n.label}</span>
                {n.heading && <span className="toc-heading">{n.heading}</span>}
              </button>
            </li>
          );
        }
        const open = Boolean(active && active.startsWith(n.path + "."));
        return (
          <li key={n.path}>
            <details open={open || undefined}>
              <summary>
                <span className="toc-label">{n.label}</span>
                {n.heading && <span className="toc-heading">{n.heading}</span>}
              </summary>
              {n.children.length > 0 && <Toc nodes={n.children} active={active} onOpen={onOpen} />}
            </details>
          </li>
        );
      })}
    </ul>
  );
}

function Article({ law, node, onOpen }: { law: string; node: NodeView; onOpen: (path: string) => void }) {
  const t = useTranslations("laws");
  const tr = useTranslations("reforms.change");
  const [versions, setVersions] = useState<LawVersion[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const lang = node.citables[0]?.cite_id.startsWith("US-") ? "en" : "es";
  useEffect(() => {
    setVersions(null);
    setShowHistory(false);
  }, [node.path]);
  const reformOf = (citeId: string) => node.reforms.find((r) => r.cite_id === citeId);
  return (
    <article className="law-article" aria-labelledby="law-node-title">
      <nav aria-label={t("trail")}>
        <ol className="trail">
          <li>{node.law_title}</li>
          {node.trail.map((x) => (
            <li key={x.path}>{x.label}</li>
          ))}
        </ol>
      </nav>
      <h2 id="law-node-title" tabIndex={-1}>
        {node.label}
      </h2>
      <div className="legal-text law-text" lang={lang}>
        {node.text}
      </div>
      {node.children.length > 0 && (
        <ul className="law-children">
          {node.children.map((c) => (
            <li key={c.path}>
              <button type="button" className="button button-quiet" onClick={() => onOpen(c.path)}>
                {c.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="law-pager">
        <button type="button" className="button button-quiet" disabled={!node.prev_path} onClick={() => node.prev_path && onOpen(node.prev_path)}>
          {t("prev")}
        </button>
        <button type="button" className="button button-quiet" disabled={!node.next_path} onClick={() => node.next_path && onOpen(node.next_path)}>
          {t("next")}
        </button>
      </div>
      {node.citables.length > 0 && (
        <section aria-labelledby="law-citables">
          <h3 id="law-citables">{t("citables")}</h3>
          <p className="small muted">{t("citablesHelp")}</p>
          <ul className="citables">
            {node.citables.map((c) => {
              const reform = reformOf(c.cite_id);
              return (
                <li key={c.cite_id} className="citable">
                  <p className="citable-head">
                    <span className="citable-label">{c.citation_string}</span>
                    {!c.is_current && <span className="badge badge-warn">{t("notCurrent")}</span>}
                    {reform && (
                      <span className="badge badge-warn">
                        <span aria-hidden="true">◷ </span>
                        {tr(reform.change)}
                        {reform.published_on ? ` · ${reform.published_on}` : ""}
                      </span>
                    )}
                  </p>
                  <p className="cite-id small">{c.cite_id}</p>
                  <div className="citable-actions">
                    <button
                      type="button"
                      className="button button-quiet"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(`${c.citation_string} [${c.cite_id}]`);
                          setCopied(c.cite_id);
                        } catch {
                          setCopied(null);
                        }
                      }}
                    >
                      {copied === c.cite_id ? t("copied") : t("copy")}
                    </button>
                    <SaveToMatter citeId={c.cite_id} />
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <section aria-labelledby="law-history">
        <h3 id="law-history">{t("history")}</h3>
        <button
          type="button"
          className="button button-quiet"
          aria-expanded={showHistory}
          onClick={async () => {
            const next = !showHistory;
            setShowHistory(next);
            if (next && !versions) setVersions(await getHistory(law, node.path).catch(() => []));
          }}
        >
          {showHistory ? t("hideHistory") : t("showHistory")}
        </button>
        {showHistory && versions && <History versions={versions} lang={lang} />}
      </section>
    </article>
  );
}

function History({ versions, lang }: { versions: LawVersion[]; lang: string }) {
  const t = useTranslations("laws");
  const current = versions.find((v) => v.current) ?? versions[versions.length - 1];
  const older = versions.filter((v) => v !== current);
  const [pick, setPick] = useState<string>(older[older.length - 1]?.document_id ?? "");
  const id = useId();
  if (older.length === 0) return <p className="small muted">{t("onlyVersion")}</p>;
  const base = older.find((v) => v.document_id === pick) ?? older[older.length - 1];
  return (
    <div className="history-box">
      <div className="field">
        <label htmlFor={id}>{t("compareWith")}</label>
        <select id={id} value={base.document_id} onChange={(e) => setPick(e.target.value)}>
          {older.map((v) => (
            <option key={v.document_id} value={v.document_id}>
              {t("versionOption", { version: v.version_label ?? "—", until: v.valid_to ?? "—" })}
            </option>
          ))}
        </select>
      </div>
      <p className="small muted">{t("diffLegend")}</p>
      {base.text === null ? (
        <p>{t("didNotExist")}</p>
      ) : current.text === null ? (
        <p>{t("noLongerExists")}</p>
      ) : (
        <div className="legal-text law-diff" lang={lang}>
          {diffWords(base.text, current.text).map((p, i) =>
            p.kind === "same" ? (
              <span key={i}>{p.text}</span>
            ) : p.kind === "added" ? (
              <ins key={i} aria-label={t("added")}>
                {p.text}
              </ins>
            ) : (
              <del key={i} aria-label={t("removed")}>
                {p.text}
              </del>
            ),
          )}
        </div>
      )}
    </div>
  );
}
