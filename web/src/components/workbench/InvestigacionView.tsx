"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import type { Message } from "@/lib/api";
import { titleFrom } from "@/lib/conversation";
import { resolve } from "@/lib/jurisdiction";
import { useCatalogs, useConversation } from "@/lib/useWorkbench";
import { AnswerView } from "../AnswerView";
import { RefusalCard } from "../RefusalCard";
import { SourceViewer } from "../SourceViewer";
import { History } from "./History";
import { Announcer, BusyElsewhere, Composer, ConversationBar, ErrorNote, Examples, RunningNote, type Seed } from "./Parts";
import { DEFAULT_SCOPE, loadScope, ScopePicker, ScopeSummary, type Scope } from "./Scope";
import { Workbench } from "./Workbench";
import { MatterPicker, useInitialMatter, useMatters } from "./MatterPicker";

/** One question and its dossier (sections I-V, Brand.md 4), with its own numbered sources. */
function Dossier({ question, answer, onOpen }: { question: string; answer: Message; onOpen: (id: string) => void }) {
  const t = useTranslations("workbench.research");
  return (
    <article className="dossier">
      <p className="eyebrow">{t("question")}</p>
      <h3 className="dossier-question">{question}</h3>
      {answer.status === "refused" ? (
        <RefusalCard reason={answer.refusal_reason} />
      ) : (
        <AnswerView text={answer.content} citations={answer.citations} onOpen={onOpen} />
      )}
    </article>
  );
}

/** Investigación (ADR-016): each question becomes a dossier; questions do not share history. */
export function InvestigacionView() {
  const t = useTranslations("workbench.research");
  const tw = useTranslations("workbench");
  const locale = useLocale();
  const { catalogs, failed } = useCatalogs();
  const w = useConversation("research");
  const [scope, setScope] = useState<Scope>(DEFAULT_SCOPE);
  const matters = useMatters();
  const [matter, setMatter] = useInitialMatter();
  const [viewing, setViewing] = useState<string | null>(null);
  const [seed, setSeed] = useState<Seed>();
  // Opening a conversation from the history moves the focus to its title (keyboard and screen
  // reader users land on what they chose, not at the top of the page).
  const openAndFocus = async (id: string) => {
    await w.open(id);
    requestAnimationFrame(() => document.getElementById("conversation-title")?.focus());
  };

  useEffect(() => {
    if (catalogs) setScope(loadScope(catalogs.rows, catalogs.personas));
  }, [catalogs]);

  const pairs: { q: Message; a: Message }[] = [];
  for (let i = 0; i + 1 < w.messages.length; i += 2) pairs.push({ q: w.messages[i], a: w.messages[i + 1] });

  return (
    <Workbench
      title={t("title")}
      failed={failed}
      ready={Boolean(catalogs)}
      history={<History kind="research" version={w.listVersion} activeId={w.activeId} onOpen={(id) => void openAndFocus(id)} onNew={() => { void w.open(null); setSeed({ text: "", n: Date.now() }); }} />}
    >
      {catalogs && (
        <>
          {w.conversation ? (
            <>
              <ConversationBar conversation={w.conversation} onRename={(x) => void w.rename(x)} onDelete={() => void w.remove()} matter={<MatterPicker compact matters={matters} value={w.conversation.matter_id} onChange={(m) => void w.move(m)} />} />
              <div className="toolbar">
                <ScopeSummary conversation={w.conversation} rows={catalogs.rows} personas={catalogs.personas} />
                <button type="button" className="button button-quiet" onClick={() => window.print()} disabled={pairs.length === 0}>
                  {t("export")}
                </button>
              </div>
            </>
          ) : (
            <section className="welcome">
              <h2>{t("welcomeTitle")}</h2>
              <p>{t("welcomeBody")}</p>
              <ScopePicker rows={catalogs.rows} personas={catalogs.personas} value={scope} onChange={setScope} />
              <MatterPicker matters={matters} value={matter} onChange={setMatter} />
              <Examples items={[t("ex1"), t("ex2")]} onPick={(text) => setSeed({ text, n: Date.now() })} />
            </section>
          )}
          {pairs.map((p) => (
            <Dossier key={p.q.seq} question={p.q.content} answer={p.a} onOpen={setViewing} />
          ))}
          {w.runningHere && (
            <article className="dossier">
              <p className="eyebrow">{t("question")}</p>
              <h3 className="dossier-question">{w.runningHere.pending}</h3>
              <RunningNote running={w.runningHere} />
            </article>
          )}
          {w.busyElsewhere && <BusyElsewhere />}
          <Announcer what={w.announce} />
          {w.error && <ErrorNote code={w.error} />}
          <div className="no-print">
            <Composer
              label={w.conversation ? t("followUpLabel") : t("composerLabel")}
              placeholder={t("placeholder")}
              buttons={[{ action: "research", label: t("send") }]}
              seed={seed}
              rows={4}
              disabled={Boolean(w.running) || (!w.conversation && !resolve(catalogs.rows, scope.selection))}
              onSubmit={(text, action) => {
                const j = resolve(catalogs.rows, scope.selection);
                return w.send(
                  text,
                  action,
                  w.conversation || !j
                    ? undefined
                    : {
                        kind: "research", title: titleFrom(text), jurisdiction_id: j.id, persona: scope.persona,
                        language: locale === "en-US" ? "en" : "es", include_historical: scope.historical, matter_id: matter,
                      },
                );
              }}
            />
            <p className="small muted">{tw("slowNote")}</p>
          </div>
        </>
      )}
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </Workbench>
  );
}
