"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { ApiError, listConversations, type Conversation, type Kind } from "@/lib/api";

/** When the user acted is a LOCAL time: the app-wide UTC zone (i18n/locales.ts) is for calendar
 *  dates such as vigencia, and would show tonight's conversation as tomorrow. Client-only list. */
function localZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}

/** The user's own conversations of one kind (Brand.md 8.2), newest first, with search. */
export function History({
  kind,
  version,
  activeId,
  onOpen,
  onNew,
}: {
  kind: Kind;
  version: number;
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const t = useTranslations("workbench.history");
  const tk = useTranslations(`workbench.${kind}`);
  const format = useFormatter();
  const searchId = useId();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      listConversations(kind, q)
        .then((rows) => {
          if (!cancelled) {
            setItems(rows);
            setFailed(false);
          }
        })
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 401) window.location.assign("/login");
          else if (!cancelled) setFailed(true);
        });
    }, q ? 250 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [kind, q, version]);

  return (
    <nav className="history" aria-label={tk("historyLabel")}>
      <button type="button" className="button history-new" onClick={onNew}>
        {tk("new")}
      </button>
      <label htmlFor={searchId} className="sr-only">
        {t("search")}
      </label>
      <input id={searchId} className="history-search" type="search" maxLength={200} placeholder={t("search")} value={q} onChange={(e) => setQ(e.target.value)} />
      {failed && <p className="small muted">{t("failed")}</p>}
      {items && items.length === 0 && <p className="small muted">{q ? t("noMatch") : tk("empty")}</p>}
      {items && items.length > 0 && (
        <ul className="history-list">
          {items.map((c) => (
            <li key={c.id}>
              <button type="button" className="history-item" aria-current={c.id === activeId ? "page" : undefined} onClick={() => onOpen(c.id)}>
                <span className="history-title">{c.title}</span>
                <span className="history-meta">
                  {format.dateTime(new Date(c.updated_at), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: localZone() })}
                  {" · "}
                  {t("messages", { count: c.message_count })}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
