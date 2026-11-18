"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { conversationSources, sourceNumbers, titleFrom } from "@/lib/conversation";
import { resolve } from "@/lib/jurisdiction";
import { takeDraft } from "@/lib/draft";
import { useCatalogs, useConversation } from "@/lib/useWorkbench";
import { SourceViewer } from "../SourceViewer";
import { History } from "./History";
import { Announcer, AssistantMessage, BusyElsewhere, Composer, ConversationBar, ErrorNote, Examples, RunningNote, SourcesPanel, UserMessage, type Seed } from "./Parts";
import { DEFAULT_SCOPE, loadScope, ScopePicker, ScopeSummary, type Scope } from "./Scope";
import { Workbench } from "./Workbench";
import { MatterPicker, useInitialMatter, useMatters } from "./MatterPicker";
import { ModelPicker, useModels } from "./ModelPicker";
import type { ModelKey } from "@/lib/api";

/** Conversar (ADR-016): short answers, follow-ups that keep the conversation's sources. */
export function ConversarView() {
  const t = useTranslations("workbench.chat");
  const tw = useTranslations("workbench");
  const locale = useLocale();
  const { catalogs, failed } = useCatalogs();
  const w = useConversation("chat");
  const [scope, setScope] = useState<Scope>(DEFAULT_SCOPE);
  const matters = useMatters();
  const [matter, setMatter] = useInitialMatter();
  const models = useModels();
  const [model, setModel] = useState<ModelKey | null>(null);
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

  // Brand.md 11.2: a question written in the start page's box runs here, once.
  useEffect(() => {
    if (!catalogs) return;
    const d = takeDraft("chat");
    if (!d) return;
    const s = loadScope(catalogs.rows, catalogs.personas);
    const j = resolve(catalogs.rows, s.selection);
    if (!j) return;
    void w.send(d.text, "ask", {
      kind: "chat", title: titleFrom(d.text), jurisdiction_id: j.id, persona: s.persona,
      language: locale === "en-US" ? "en" : "es", include_historical: s.historical, matter_id: d.matter,
      ...(d.model ? { model_profile: d.model } : {}),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs when the catalogs arrive
  }, [catalogs]);

  const sources = conversationSources(w.messages);
  const numbers = sourceNumbers(sources);
  return (
    <Workbench
      title={t("title")}
      failed={failed}
      ready={Boolean(catalogs)}
      history={<History kind="chat" version={w.listVersion} activeId={w.activeId} onOpen={(id) => void openAndFocus(id)} onNew={() => { void w.open(null); setSeed({ text: "", n: Date.now() }); }} />}
      aside={<SourcesPanel sources={sources} onOpen={setViewing} />}
    >
      {catalogs && (
        <>
          {w.conversation ? (
            <>
              <ConversationBar conversation={w.conversation} onRename={(x) => void w.rename(x)} onDelete={() => void w.remove()} matter={<MatterPicker compact matters={matters} value={w.conversation.matter_id} onChange={(m) => void w.move(m)} />} />
              <ScopeSummary conversation={w.conversation} rows={catalogs.rows} personas={catalogs.personas} />
            </>
          ) : (
            <section className="welcome">
              <h2>{t("welcomeTitle")}</h2>
              <p>{t("welcomeBody")}</p>
              <ScopePicker rows={catalogs.rows} personas={catalogs.personas} value={scope} onChange={setScope} />
              <MatterPicker matters={matters} value={matter} onChange={setMatter} />
              <ModelPicker info={models} value={model} onChange={setModel} />
              <Examples items={[t("ex1"), t("ex2"), t("ex3")]} onPick={(text) => setSeed({ text, n: Date.now() })} />
            </section>
          )}
          <ol className="thread" aria-label={tw("threadLabel")}>
            {w.messages.map((m) => (
              <li key={m.seq} className={m.role === "user" ? "turn turn-user" : "turn turn-assistant"}>
                {m.role === "user" ? <UserMessage text={m.content} label={tw("you")} /> : <AssistantMessage message={m} onOpen={setViewing} numbers={numbers} />}
              </li>
            ))}
            {w.runningHere && (
              <li className="turn turn-user">
                <UserMessage text={w.runningHere.pending} label={tw("you")} />
              </li>
            )}
          </ol>
          {w.runningHere && <RunningNote running={w.runningHere} />}
          {w.busyElsewhere && <BusyElsewhere />}
          <Announcer what={w.announce} />
          {w.error && <ErrorNote code={w.error} />}
          <Composer
            label={t("composerLabel")}
            placeholder={w.conversation ? t("followUpPlaceholder") : t("placeholder")}
            buttons={[{ action: "ask", label: t("send") }]}
            seed={seed}
            disabled={Boolean(w.running) || (!w.conversation && !resolve(catalogs.rows, scope.selection))}
            onSubmit={(text, action) => {
              const j = resolve(catalogs.rows, scope.selection);
              return w.send(
                text,
                action,
                w.conversation || !j
                  ? undefined
                  : {
                      kind: "chat", title: titleFrom(text), jurisdiction_id: j.id, persona: scope.persona,
                      language: locale === "en-US" ? "en" : "es", include_historical: scope.historical, matter_id: matter,
                      ...(model ? { model_profile: model } : {}),
                    },
              );
            }}
          />
        </>
      )}
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </Workbench>
  );
}
