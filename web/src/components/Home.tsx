"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { listConversations, type Conversation, type Kind, type ModelKey } from "@/lib/api";
import { saveDraft } from "@/lib/draft";
import { useDateTime } from "@/lib/format";
import { resolve } from "@/lib/jurisdiction";
import { useCatalogs } from "@/lib/useWorkbench";
import { AlertBanner } from "./tools/ReformasView";
import { MatterPicker, useMatters } from "./workbench/MatterPicker";
import { ModelPicker, useModels } from "./workbench/ModelPicker";
import { DEFAULT_SCOPE, loadScope, ScopePicker, type Scope } from "./workbench/Scope";

type Mode = "ask" | "research" | "case";
const MODES: Mode[] = ["ask", "research", "case"];
const KIND: Record<Mode, Kind> = { ask: "chat", research: "research", case: "case" };
export const PAGE: Record<Kind, string> = { chat: "conversar", research: "investigacion", case: "argumentar" };
const MAX = 4000;

/** Brand.md 11.2: one box for every kind of work. Sending opens the workspace of the chosen mode,
 *  where the turn runs (or the case form is prefilled); nothing is sent from here. */
export function Home() {
  const t = useTranslations("home");
  const router = useRouter();
  const { catalogs } = useCatalogs();
  const matters = useMatters();
  const models = useModels();
  const [mode, setMode] = useState<Mode>("ask");
  const [text, setText] = useState("");
  const [scope, setScope] = useState<Scope>(DEFAULT_SCOPE);
  const [matter, setMatter] = useState<string | null>(null);
  const [model, setModel] = useState<ModelKey | null>(null);
  const inputId = useId();
  const helpId = useId();
  useEffect(() => {
    if (catalogs) setScope(loadScope(catalogs.rows, catalogs.personas));
  }, [catalogs]);

  const ready = Boolean(catalogs && resolve(catalogs.rows, scope.selection));
  const go = () => {
    const value = text.trim();
    if (!value || !ready) return;
    const kind = KIND[mode];
    saveDraft({ kind, text: value, matter, model, at: Date.now() });
    router.push(`/${PAGE[kind]}`);
  };

  return (
    <div className="home">
      <section className="home-hero">
        <div className="home-hero-inner">
          <p className="eyebrow">{t("eyebrow")}</p>
          <h1>{t.rich("title", { em: (c) => <em>{c}</em> })}</h1>
          <form
            className="mesa"
            onSubmit={(e) => {
              e.preventDefault();
              go();
            }}
          >
            <fieldset className="mesa-modes">
              <legend className="sr-only">{t("modeLegend")}</legend>
              {MODES.map((m) => (
                <label key={m} className={m === mode ? "mesa-mode mesa-mode-on" : "mesa-mode"}>
                  <input type="radio" name="mesa-mode" value={m} checked={m === mode} onChange={() => setMode(m)} />
                  {t(`mode.${m}`)}
                </label>
              ))}
            </fieldset>
            <label htmlFor={inputId} className="mesa-label">
              {t(`input.${mode}`)}
            </label>
            <textarea
              id={inputId}
              className="mesa-input"
              aria-describedby={helpId}
              rows={3}
              maxLength={MAX}
              value={text}
              placeholder={t(`placeholder.${mode}`)}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  go();
                }
              }}
            />
            <p id={helpId} className="small muted mesa-help">
              {t(`modeHelp.${mode}`)}
            </p>
            {catalogs && (
              <div className="mesa-options">
                <ScopePicker rows={catalogs.rows} personas={catalogs.personas} value={scope} onChange={setScope} />
                <MatterPicker compact matters={matters} value={matter} onChange={setMatter} />
                <ModelPicker compact info={models} value={model} onChange={setModel} />
              </div>
            )}
            <div className="mesa-row">
              <button type="submit" className="button button-accent" disabled={!text.trim() || !ready}>
                {t(`submit.${mode}`)}
              </button>
            </div>
          </form>
          <p className="home-trust">{t("intro")}</p>
        </div>
      </section>
      <div className="home-below">
        <AlertBanner />
        <RecentWork />
      </div>
    </div>
  );
}

/** The newest work of the three kinds together (Brand.md 11.2), up to six cards. */
function RecentWork() {
  const t = useTranslations("home");
  const th = useTranslations("workbench.history");
  const when = useDateTime();
  const [items, setItems] = useState<Conversation[] | null>(null);
  useEffect(() => {
    let live = true;
    Promise.all((["chat", "research", "case"] as Kind[]).map((k) => listConversations(k).catch(() => [] as Conversation[])))
      .then((lists) => {
        const all = lists.flat().sort((a, b) => b.updated_at.localeCompare(a.updated_at));
        if (live) setItems(all.slice(0, 6));
      })
      .catch(() => live && setItems([]));
    return () => {
      live = false;
    };
  }, []);
  return (
    <section aria-labelledby="home-recent" className="recent">
      <h2 id="home-recent">{t("recentTitle")}</h2>
      {items && items.length === 0 && <p className="small muted">{t("recentEmpty")}</p>}
      {items && items.length > 0 && (
        <ul className="recent-list">
          {items.map((c) => (
            <li key={c.id}>
              <Link className="recent-card" data-kind={c.kind} href={`/${PAGE[c.kind]}?c=${c.id}`}>
                <span className="recent-kind">{t(`kind.${c.kind}`)}</span>
                <span className="recent-title">{c.title}</span>
                <span className="small muted">
                  {th("messages", { count: c.message_count })} · {when(c.updated_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
