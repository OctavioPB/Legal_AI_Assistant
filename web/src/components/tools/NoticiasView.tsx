"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { getNews, type NewsFeed, type NewsItem } from "@/lib/api";
import { useDateTime } from "@/lib/format";

const FILTERS = {
  all: null,
  law: ["reform", "new_law", "recent_reform"],
  court: ["court"],
  press: ["press"],
} as const satisfies Record<string, readonly NewsItem["kind"][] | null>;
type Filter = keyof typeof FILTERS;

/** ADR-027 (Brand.md 9.8): the shared legal news feed. Read only; chosen by fixed rules; every title
 *  and excerpt is the source's own text, rendered as text. */
export function NoticiasView() {
  const t = useTranslations("news");
  const when = useDateTime();
  const [feed, setFeed] = useState<NewsFeed | null>(null);
  const [failed, setFailed] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => {
    getNews()
      .then(setFeed)
      .catch(() => setFailed(true));
  }, []);
  const kinds = FILTERS[filter];
  const items = feed?.items.filter((i) => kinds === null || (kinds as readonly string[]).includes(i.kind)) ?? [];

  return (
    <div className="tool-page">
      <header className="tool-head">
        <h1>{t("title")}</h1>
        <p className="muted">{t("intro")}</p>
        {feed?.refreshed_at && <p className="small muted">{t("refreshed", { when: when(feed.refreshed_at) })}</p>}
      </header>
      {failed && (
        <p className="form-status" role="alert">
          {t("loadError")}
        </p>
      )}
      {feed && feed.items.length === 0 && <p className="small muted">{t("never")}</p>}
      {feed && feed.items.length > 0 && (
        <>
          <div className="news-filters" role="group" aria-label={t("show")}>
            {(Object.keys(FILTERS) as Filter[]).map((f) => (
              <button key={f} type="button" className="button button-quiet" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {t(`filter.${f}`)}
              </button>
            ))}
          </div>
          {items.length === 0 ? (
            <p className="small muted">{t("none")}</p>
          ) : (
            <ol className="news-list">
              {items.map((i) => (
                <NewsCard key={i.id} item={i} />
              ))}
            </ol>
          )}
        </>
      )}
      {feed && (
        <section aria-labelledby="news-sources">
          <h2 id="news-sources">{t("sources")}</h2>
          <p className="small muted">{t("sourcesHelp")}</p>
          <table className="checks">
            <thead>
              <tr>
                <th scope="col">{t("source")}</th>
                <th scope="col">{t("checkedAt")}</th>
                <th scope="col">{t("result")}</th>
              </tr>
            </thead>
            <tbody>
              {feed.sources.map((s) => (
                <tr key={s.source_id}>
                  <td>{s.outlet}</td>
                  <td>{s.checked_at ? when(s.checked_at) : t("notYet")}</td>
                  <td>{s.outcome ? t(`outcome.${s.outcome}`) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function NewsCard({ item }: { item: NewsItem }) {
  const t = useTranslations("news");
  const tc = useTranslations("reforms.change");
  const when = useDateTime();
  const day = (iso: string) => when(iso).replace(/,?\s+\d{1,2}:\d{2}.*$/, "");
  const internal = item.url.startsWith("/");
  const counts = Object.entries(item.facts.counts ?? {}).filter(([, n]) => n > 0);
  return (
    <li className="news-item">
      <p className="news-meta small">
        <span className="badge news-kind">{t(`kind.${item.kind}`)}</span>
        {item.official && <span className="badge news-official">{t("official")}</span>}
        <span className="muted">
          {item.outlet} · {item.published_at ? day(item.published_at) : t("seen", { date: day(item.first_seen_at) })}
        </span>
      </p>
      <h2 className="news-title">
        {internal ? (
          <Link href={item.url}>{item.title}</Link>
        ) : (
          <a href={item.url} target="_blank" rel="noreferrer noopener">
            {item.title}
          </a>
        )}
      </h2>
      {item.excerpt && <p className="news-excerpt">{item.excerpt}</p>}
      {counts.length > 0 && <p className="small">{counts.map(([k, n]) => `${tc(k)}: ${n}`).join(" · ")}</p>}
      {item.facts.last_reform && item.kind !== "reform" && (
        <p className="small muted">
          {t("lastReform", { text: item.facts.last_reform })}
          {item.facts.note ? ` · ${item.facts.note}` : ""}
        </p>
      )}
      {item.terms.length > 0 && <p className="small muted">{t("why", { terms: item.terms.join(", ") })}</p>}
      <p className="small">
        {internal ? (
          <Link href={item.url}>{t("openInApp")}</Link>
        ) : (
          <a href={item.url} target="_blank" rel="noreferrer noopener">
            {t("openSource")}
          </a>
        )}
      </p>
    </li>
  );
}
