"use client";

import { useTranslations } from "next-intl";

export function Footer() {
  const t = useTranslations("footer");
  return (
    <footer className="app-footer">
      <p>{t("disclaimer")}</p>
    </footer>
  );
}
