"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";
import { LanguageToggle } from "./LanguageToggle";
import { ModelPolicy } from "./ModelPolicy";
import type { Theme } from "@/lib/theme";
import { ThemeToggle } from "./ThemeToggle";

const WORK = ["conversar", "investigacion", "argumentar"] as const;
const TOOLS = ["leyes", "verificar", "asuntos", "reformas"] as const;

// Stroke icons (decorative: every link has its visible text). Brand.md 11.1.
const ICON: Record<string, string> = {
  home: "M4 11l8-7 8 7v9H4z M10 20v-6h4v6",
  conversar: "M4 5h16v11H8l-4 4z",
  investigacion: "M11 4a7 7 0 1 1 0 14a7 7 0 0 1 0-14z M16 16l4 4",
  argumentar: "M12 4v16 M5 8h14 M5 8l-2 6h4z M19 8l-2 6h4z",
  leyes: "M5 4h11l3 3v13H5z M8 10h8 M8 14h8",
  verificar: "M5 12l5 5L20 7",
  asuntos: "M3 7h18v12H3z M8 7V4h8v3",
  reformas: "M12 4a8 8 0 1 1 0 16a8 8 0 0 1 0-16z M12 8v4l3 2",
  settings: "M12 9a3 3 0 1 1 0 6a3 3 0 0 1 0-6z M12 3v3 M12 18v3 M3 12h3 M18 12h3",
};

function Icon({ name }: { name: string }) {
  return (
    <svg className="nav-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={ICON[name]} />
    </svg>
  );
}

/** Brand.md 11.1: the navy side rail (home, the three workspaces, the tools, settings). On the login
 *  page there is no navigation: only the wordmark and "Ajustes". Language, theme, the firm's model
 *  policy and sign-out live in the "Ajustes" side panel (a native <dialog>: focus trap and Esc). */
export function Header({ initialTheme }: { initialTheme: Theme }) {
  const t = useTranslations();
  const path = usePathname() ?? "/";
  const signedIn = !path.startsWith("/login");
  const dialog = useRef<HTMLDialogElement>(null);
  const [opened, setOpened] = useState(false); // load the model policy only once the panel opens
  async function logout() {
    await fetch("/api/session", { method: "DELETE" });
    window.location.assign("/login");
  }
  const current = (p: string) => (p === "" ? path === "/" : path.startsWith(`/${p}`)) ? "page" : undefined;
  const settingsButton = (
    <button
      type="button"
      className="settings-open"
      aria-haspopup="dialog"
      onClick={() => {
        setOpened(true);
        dialog.current?.showModal();
      }}
    >
      <Icon name="settings" />
      {t("nav.settings")}
    </button>
  );
  const wordmark = (
    <a className="wordmark" href={signedIn ? "/" : "/login"} aria-label={t("brand.name")}>
      <span aria-hidden="true">{t.rich("brand.wordmark", { em: (c) => <em>{c}</em> })}</span>
    </a>
  );
  return (
    <>
      <a className="skip-link" href="#main">
        {t("nav.skip")}
      </a>
      {signedIn ? (
        <aside className="app-nav">
          {wordmark}
          <nav aria-label={t("nav.sections")} className="nav-groups">
            <Link href="/" className="nav-link" aria-current={current("")}>
              <Icon name="home" />
              {t("nav.home")}
            </Link>
            <p className="nav-group" id="nav-work">
              {t("nav.work")}
            </p>
            <ul className="nav-list" aria-labelledby="nav-work">
              {WORK.map((p) => (
                <li key={p}>
                  <Link href={`/${p}`} className="nav-link nav-sub" aria-current={current(p)}>
                    <Icon name={p} />
                    {t(`nav.${p}`)}
                  </Link>
                </li>
              ))}
            </ul>
            <p className="nav-group" id="nav-tools">
              {t("nav.tools")}
            </p>
            <ul className="nav-list" aria-labelledby="nav-tools">
              {TOOLS.map((p) => (
                <li key={p}>
                  <Link href={`/${p}`} className="nav-link" aria-current={current(p)}>
                    <Icon name={p} />
                    {t(`nav.${p}`)}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          {settingsButton}
        </aside>
      ) : (
        <header className="app-header">
          {wordmark}
          {settingsButton}
        </header>
      )}
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
        {signedIn && opened && <ModelPolicy />}
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
