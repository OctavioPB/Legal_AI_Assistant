"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { getReformDetail, getReforms, listAlerts, type Alert, type LineageRow, type ReformOverview } from "@/lib/api";
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
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </div>
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
