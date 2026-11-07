"use client";

import { useTranslations } from "next-intl";
import { LanguageToggle } from "./LanguageToggle";
import type { Theme } from "@/lib/theme";
import { ThemeToggle } from "./ThemeToggle";

export function Header({ initialTheme }: { initialTheme: Theme }) {
  const t = useTranslations();
  return (
    <>
      <a className="skip-link" href="#main">
        {t("nav.skip")}
      </a>
      <header className="app-header">
        <a className="wordmark" href="/">
          {t("brand.name")}
        </a>
        <div className="header-controls">
          <LanguageToggle />
          <ThemeToggle initialTheme={initialTheme} />
        </div>
      </header>
    </>
  );
}
