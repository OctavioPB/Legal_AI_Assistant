"use client";

import { useTranslations } from "next-intl";
import { LOCALES } from "@/i18n/locales";
import { useLocaleSwitch } from "./LocaleShell";

export function LanguageToggle() {
  const t = useTranslations();
  const { locale, setLocale } = useLocaleSwitch();
  return (
    <div className="segmented" role="group" aria-label={t("nav.language")}>
      {LOCALES.map((code) => (
        <button
          key={code}
          type="button"
          lang={code === "es-MX" ? "es" : "en"}
          aria-pressed={locale === code}
          onClick={() => setLocale(code)}
        >
          {t(`lang.${code}`)}
        </button>
      ))}
    </div>
  );
}
