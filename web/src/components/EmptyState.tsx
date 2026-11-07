"use client";

import { useTranslations } from "next-intl";

export function EmptyState() {
  const t = useTranslations("home.empty");
  return (
    <section className="empty-state" aria-labelledby="empty-title">
      <h1 id="empty-title">{t("title")}</h1>
      <p>{t("body")}</p>
    </section>
  );
}
