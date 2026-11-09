"use client";

import { NextIntlClientProvider } from "next-intl";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { CATALOGS, TIME_ZONE, type Locale } from "@/i18n/locales";

interface LocaleContextValue {
  locale: Locale;
  setLocale: (next: Locale) => void;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

/** Holds the active locale on the client so switching language re-renders in place (no reload). */
export function LocaleShell({ initialLocale, children }: { initialLocale: Locale; children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    document.documentElement.lang = next;
    document.cookie = `locale=${next}; path=/; max-age=31536000; samesite=strict`;
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  return (
    <LocaleContext.Provider value={value}>
      <NextIntlClientProvider locale={locale} messages={CATALOGS[locale]} timeZone={TIME_ZONE}>
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}

export function useLocaleSwitch(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) throw new Error("useLocaleSwitch must be used inside LocaleShell");
  return ctx;
}
