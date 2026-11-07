import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { LocaleShell } from "@/components/LocaleShell";
import { CATALOGS, DEFAULT_LOCALE, isLocale } from "@/i18n/locales";
import { isTheme, type Theme } from "@/lib/theme";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const jar = await cookies();
  const raw = jar.get("locale")?.value;
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return { title: CATALOGS[locale].brand.name, description: CATALOGS[locale].brand.tagline };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const jar = await cookies();
  const rawLocale = jar.get("locale")?.value;
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const rawTheme = jar.get("theme")?.value;
  const theme: Theme = isTheme(rawTheme) ? rawTheme : "system";
  // The theme comes from a cookie so the first paint is right without an inline script (CSP).
  return (
    <html lang={locale} data-theme={theme === "system" ? undefined : theme}>
      <body>
        <LocaleShell initialLocale={locale}>
          <Header initialTheme={theme} />
          <main id="main">{children}</main>
          <Footer />
        </LocaleShell>
      </body>
    </html>
  );
}
