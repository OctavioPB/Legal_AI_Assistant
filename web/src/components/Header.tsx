"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useRef } from "react";
import { LanguageToggle } from "./LanguageToggle";
import type { Theme } from "@/lib/theme";
import { ThemeToggle } from "./ThemeToggle";

const PAGES = ["conversar", "investigacion", "argumentar", "leyes", "verificar", "asuntos"] as const;

/** Top bar (Brand.md 8.1, 10): the OPB navy band with the wordmark and the pages. Language, theme
 *  and sign-out live in the "Ajustes" side panel (a native <dialog>: focus trap and Esc for free).
 *  The navigation is hidden on the login page. */
export function Header({ initialTheme }: { initialTheme: Theme }) {
  const t = useTranslations();
  const path = usePathname() ?? "/";
  const signedIn = !path.startsWith("/login");
  const dialog = useRef<HTMLDialogElement>(null);
  async function logout() {
    await fetch("/api/session", { method: "DELETE" });
    window.location.assign("/login");
  }
  return (
    <>
      <a className="skip-link" href="#main">
        {t("nav.skip")}
      </a>
      <header className="app-header">
        <a className="wordmark" href={signedIn ? "/" : "/login"} aria-label={t("brand.name")}>
          <span aria-hidden="true">{t.rich("brand.wordmark", { em: (c) => <em>{c}</em> })}</span>
        </a>
        {signedIn && (
          <nav className="pages" aria-label={t("nav.sections")}>
            {PAGES.map((p) => (
              <Link key={p} href={`/${p}`} className="page-link" aria-current={path.startsWith(`/${p}`) ? "page" : undefined}>
                {t(`nav.${p}`)}
              </Link>
            ))}
          </nav>
        )}
        <button type="button" className="settings-open" aria-haspopup="dialog" onClick={() => dialog.current?.showModal()}>
          {t("nav.settings")}
        </button>
      </header>
      <dialog ref={dialog} className="settings-dialog" aria-labelledby="settings-title">
        <div className="settings-head">
          <h2 id="settings-title">{t("settings.title")}</h2>
          <button type="button" className="button button-quiet" onClick={() => dialog.current?.close()}>
            {t("settings.close")}
          </button>
        </div>
        <section className="settings-group" aria-labelledby="settings-language">
          <h3 id="settings-language">{t("nav.language")}</h3>
          <LanguageToggle />
        </section>
        <section className="settings-group" aria-labelledby="settings-theme">
          <h3 id="settings-theme">{t("nav.theme")}</h3>
          <ThemeToggle initialTheme={initialTheme} />
        </section>
        {signedIn && (
          <section className="settings-group" aria-labelledby="settings-session">
            <h3 id="settings-session">{t("settings.session")}</h3>
            <button type="button" className="button button-quiet" onClick={logout}>
              {t("session.logout")}
            </button>
          </section>
        )}
      </dialog>
    </>
  );
}
