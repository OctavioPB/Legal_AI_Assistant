"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { listMatters, type Matter } from "@/lib/api";

/** The open matters the user can see (ADR-018). Failing to load them leaves only "No matter". */
export function useMatters(): Matter[] {
  const [matters, setMatters] = useState<Matter[]>([]);
  useEffect(() => {
    let live = true;
    listMatters()
      .then((m) => live && setMatters(m.filter((x) => x.status === "open")))
      .catch(() => live && setMatters([]));
    return () => {
      live = false;
    };
  }, []);
  return matters;
}

/** `?matter=` preselects a matter (the "new conversation in this matter" links of /asuntos). */
export function useInitialMatter(): [string | null, (m: string | null) => void] {
  const [matter, setMatter] = useState<string | null>(null);
  useEffect(() => {
    // read once on mount (no useSearchParams: it would need a Suspense boundary on every page)
    const m = new URLSearchParams(window.location.search).get("matter");
    if (m && /^[0-9a-f-]{36}$/.test(m)) setMatter(m);
  }, []);
  return [matter, setMatter];
}

/** Optional matter of a conversation (Brand.md 9.5): a plain select, "No matter" by default. */
export function MatterPicker({
  value,
  onChange,
  matters,
  compact = false,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  matters: Matter[];
  compact?: boolean;
}) {
  const t = useTranslations("matters.picker");
  const id = useId();
  const known = value === null || matters.some((m) => m.id === value);
  if (matters.length === 0 && value === null) return null; // nothing to choose: no noise for new users
  return (
    <div className={compact ? "field field-inline" : "field"}>
      <label htmlFor={id}>{t("label")}</label>
      <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{t("none")}</option>
        {!known && value && <option value={value}>{t("unavailable")}</option>}
        {matters.map((m) => (
          <option key={m.id} value={m.id}>
            {m.client_ref ? `${m.title} · ${m.client_ref}` : m.title}
          </option>
        ))}
      </select>
      {!compact && <p className="small muted">{t("help")}</p>}
    </div>
  );
}
