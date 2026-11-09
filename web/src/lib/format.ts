"use client";

import { useLocale } from "next-intl";
import { useMemo } from "react";

/** Dates in the interface language and the user's own time zone ("8 nov 2026, 19:31" /
 *  "Nov 8, 2026, 7:31 PM"); the browser default could be another language. */
export function useDateTime(): (iso: string) => string {
  const locale = useLocale();
  return useMemo(() => {
    const fmt = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" });
    return (iso: string) => {
      const d = new Date(iso);
      return Number.isNaN(d.getTime()) ? iso : fmt.format(d);
    };
  }, [locale]);
}
