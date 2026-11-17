"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import {
  getDiscovery,
  getReformDetail,
  getReforms,
  listAlerts,
  type Alert,
  type Discovery,
  type IndexEntry,
  type LineageRow,
  type ReformOverview,
} from "@/lib/api";
import { useDateTime } from "@/lib/format";
import { SourceViewer } from "../SourceViewer";

const PAGE_OF = { chat: "conversar", research: "investigacion", case: "argumentar" } as const;

export function alertHref(a: Alert): string {
  if (a.where === "conversation" && a.kind) return `/${PAGE_OF[a.kind]}?c=${a.target_id}`;
  if (a.where === "matter_source") return `/asuntos?m=${a.target_id}`;
  return a.matter_id ? `/asuntos?m=${a.matter_id}` : "/verificar";
}

/** Feature 6 (ADR-017, Brand.md 9.4): what the reform watcher found, and what of the user's work
 *  cites a unit that a reform amended, derogated or removed. */
export function ReformasView() {
  const t = useTranslations("reforms");
  const tc = useTranslations("reforms.change");
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [overview, setOverview] = useState<ReformOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const when = useDateTime();
  useEffect(() => {
    Promise.all([listAlerts(), getReforms()])
      .then(([a, o]) => {
        setAlerts(a);
        setOverview(o);
      })
      .catch(() => setFailed(true));
  }, []);

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
      <section aria-labelledby="reform-alerts">
        <h2 id="reform-alerts">{t("alerts")}</h2>
        {alerts && alerts.length === 0 && <p className="small muted">{t("noAlerts")}</p>}
        {alerts && alerts.length > 0 && (
          <ul className="alert-list">
            {alerts.map((a, i) => (
              <li key={`${a.where}-${a.target_id}-${a.cite_id}-${i}`} className="alert-item">
                <p className="citable-head">
                  <span className="badge badge-warn">
                    <span aria-hidden="true">◷ </span>
                    {tc(a.change)}
                    {a.published_on ? ` · ${a.published_on}` : ""}
                  </span>
                  <span className="citable-label">{a.citation_string}</span>
                </p>
                <p className="small">
                  {t(`where.${a.where}`)}: <Link href={alertHref(a)}>{a.target_title}</Link>
                </p>
                <p className="small">
                  <button type="button" className="button button-quiet" onClick={() => setViewing(a.cite_id)}>
                    {t("openCited")}
                  </button>
                  {a.current_cite_id && (
                    <button type="button" className="button button-quiet" onClick={() => setViewing(a.current_cite_id)}>
                      {t("openCurrent")}
                    </button>
                  )}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="reform-changes">
        <h2 id="reform-changes">{t("changes")}</h2>
        {overview && overview.changes.length === 0 && <p className="small muted">{t("noChanges")}</p>}
        <ul className="matter-items">
          {overview?.changes.map((c) => <ChangeItem key={c.id} change={c} onOpen={setViewing} />)}
        </ul>
      </section>
      <section aria-labelledby="reform-checks">
        <h2 id="reform-checks">{t("checks")}</h2>
        <p className="small muted">{t("checksHelp")}</p>
        {overview && overview.checks.length === 0 ? (
          <p className="small muted">{t("noChecks")}</p>
        ) : (
          <table className="checks">
            <thead>
              <tr>
                <th scope="col">{t("law")}</th>
                <th scope="col">{t("checkedAt")}</th>
                <th scope="col">{t("outcomeLabel")}</th>
              </tr>
            </thead>
            <tbody>
              {overview?.checks.map((c) => (
                <tr key={c.official_id}>
                  <td>{c.title ?? c.official_id}</td>
                  <td>{when(c.checked_at)}</td>
                  <td>{t(`outcome.${c.outcome}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <DiscoverySection />
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

const fold = (s: string) =>
  s
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

/** ADR-026: what the official indexes list. Read only: approving a law writes the public corpus,
 *  shared by every firm, so it is an operator's command, never a button here. */
function DiscoverySection() {
  const t = useTranslations("reforms.discovery");
  const when = useDateTime();
  const [data, setData] = useState<Discovery | null>(null);
  const [query, setQuery] = useState("");
  const [showCatalog, setShowCatalog] = useState(false);
  useEffect(() => {
    getDiscovery()
      .then(setData)
      .catch(() => setData(null));
  }, []);
  if (!data) return null;
  const fresh = data.entries.filter((e) => e.status === "new");
  const approved = data.entries.filter((e) => e.status === "approved");
  const words = fold(query).split(/\s+/).filter(Boolean);
  const catalog = data.entries.filter((e) => words.every((w) => fold(e.title).includes(w)));
  return (
    <section aria-labelledby="reform-indexes">
      <h2 id="reform-indexes">{t("title")}</h2>
      <p className="small muted">{t("intro")}</p>
      {data.indexes.map((i) => (
        <p key={i.index_id} className="small">
          <strong>{i.title}</strong> · {i.checked_at ? t("lastRead", { when: when(i.checked_at) }) : t("neverRead")}
        </p>
      ))}
      <h3>{t("fresh")}</h3>
      {fresh.length === 0 ? <p className="small muted">{t("noFresh")}</p> : <EntryList entries={fresh} />}
      {approved.length > 0 && (
        <>
          <h3>{t("approved")}</h3>
          <EntryList entries={approved} />
        </>
      )}
      {data.entries.length > 0 && (
        <>
          <button type="button" className="button button-quiet" aria-expanded={showCatalog} onClick={() => setShowCatalog(!showCatalog)}>
            {t("catalog", { n: data.entries.length })}
          </button>
          {showCatalog && (
            <>
              <div className="field">
                <label htmlFor="index-filter">{t("filter")}</label>
                <input id="index-filter" type="search" value={query} maxLength={100} onChange={(e) => setQuery(e.target.value)} />
              </div>
              {catalog.length === 0 ? <p className="small muted">{t("noMatch")}</p> : <EntryList entries={catalog.slice(0, 400)} />}
            </>
          )}
        </>
      )}
    </section>
  );
}

function EntryList({ entries }: { entries: IndexEntry[] }) {
  const t = useTranslations("reforms.discovery");
  return (
    <ul className="matter-items">
      {entries.map((e) => (
        <li key={`${e.index_id}:${e.entry_key}`}>
          <p>
            <strong>{e.title}</strong>{" "}
            <span className="badge">{e.loaded ? t("loaded") : e.status === "approved" ? t("pending") : t(`status.${e.status}`)}</span>
            {e.missing && <span className="badge badge-warn"> {t("missing")}</span>}
          </p>
          <p className="small muted">
            {e.last_reform_text ? t("lastReform", { text: e.last_reform_text }) : ""}
            {e.note ? ` · ${e.note}` : ""} ·{" "}
            <a href={e.url} target="_blank" rel="noreferrer noopener">
              {t("official")}
            </a>
          </p>
          {!e.loaded && (e.status === "new" || e.status === "available") && (
            <p className="small">
              <code>{t("howTo", { ref: `${e.index_id}:${e.entry_key}` })}</code>
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

function ChangeItem({ change, onOpen }: { change: ReformOverview["changes"][number]; onOpen: (id: string) => void }) {
  const t = useTranslations("reforms");
  const tc = useTranslations("reforms.change");
  const [rows, setRows] = useState<LineageRow[] | null>(null);
  const [open, setOpen] = useState(false);
  const kinds = ["amended", "added", "derogated", "removed"] as const;
  return (
    <li>
      <p>
        <strong>{change.title}</strong>{" "}
        <span className="small muted">
          · {t("versions", { from: change.old_version ?? "—", to: change.new_version ?? "—" })}
          {change.published_on ? ` · ${t("published", { date: change.published_on })}` : ""}
        </span>
      </p>
      <p className="small">
        {kinds
          .filter((k) => (change.counts[k] ?? 0) > 0)
          .map((k) => `${tc(k)}: ${change.counts[k]}`)
          .join(" · ")}
      </p>
      <button
        type="button"
        className="button button-quiet"
        aria-expanded={open}
        onClick={async () => {
          setOpen(!open);
          if (!rows) setRows(await getReformDetail(change.id).catch(() => []));
        }}
      >
        {open ? t("hideDetail") : t("showDetail")}
      </button>
      {open && rows && (
        <ul className="lineage">
          {rows.slice(0, 300).map((r, i) => (
            <li key={i}>
              <span className="badge">{tc(r.change)}</span> {r.citation_string}{" "}
              {r.new_cite_id && (
                <button type="button" className="button button-quiet" onClick={() => onOpen(r.new_cite_id!)}>
                  {t("openCurrent")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** Home banner: shown only when something of the user's work cites a reformed unit. */
export function AlertBanner() {
  const t = useTranslations("reforms");
  const [n, setN] = useState(0);
  useEffect(() => {
    listAlerts()
      .then((a) => setN(a.length))
      .catch(() => setN(0));
  }, []);
  if (n === 0) return null;
  return (
    <section className="alert-banner" aria-labelledby="alert-banner">
      <h2 id="alert-banner">
        <span aria-hidden="true">◷ </span>
        {t("bannerTitle")}
      </h2>
      <p>{t("bannerBody", { n })}</p>
      <Link className="button" href="/reformas">
        {t("bannerAction")}
      </Link>
    </section>
  );
}
