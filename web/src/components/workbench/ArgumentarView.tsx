"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { SIDES, type Conversation, type Message, type ModelKey, type Side } from "@/lib/api";
import { closingKind, conversationSources, sourceNumbers, splitClosingList, TITLE_MAX } from "@/lib/conversation";
import { resolve } from "@/lib/jurisdiction";
import { takeDraft } from "@/lib/draft";
import { useCatalogs, useConversation } from "@/lib/useWorkbench";
import { AnswerView } from "../AnswerView";
import { SourceViewer } from "../SourceViewer";
import { History } from "./History";
import { Announcer, AssistantMessage, BusyElsewhere, Composer, ConversationBar, ErrorNote, RunningNote, SourcesPanel, UserMessage, type Seed } from "./Parts";
import { DEFAULT_SCOPE, loadScope, ScopePicker, ScopeSummary, type Scope } from "./Scope";
import { Workbench } from "./Workbench";
import { MatterPicker, useInitialMatter, useMatters } from "./MatterPicker";
import { isRemote, ModelPicker, useModels } from "./ModelPicker";

const CASE_MAX = 8000;

/** The facts the rounds are about. Collapsible: long facts must not push the rounds away. */
function CaseCard({ conversation }: { conversation: Conversation }) {
  const t = useTranslations("workbench.case");
  const c = conversation.case;
  if (!c) return null;
  return (
    <details className="case-card" open>
      <summary>
        {t("cardTitle")} · {t(`side.${c.side}`)}
      </summary>
      <dl>
        <dt>{t("facts")}</dt>
        <dd className="msg-text">{c.facts}</dd>
        {c.claim && (
          <>
            <dt>{t("claim")}</dt>
            <dd className="msg-text">{c.claim}</dd>
          </>
        )}
      </dl>
    </details>
  );
}

/** A round's answer: the body, then its closing list as a numbered panel (Brand.md 3.3). */
function RoundAnswer({ message, onOpen, numbers }: { message: Message; onOpen: (id: string) => void; numbers: Map<string, number> }) {
  const t = useTranslations("workbench.case");
  const titleId = useId();
  const heading = message.action === "attack" ? t("attackTitle") : t("strengthenTitle");
  if (message.status !== "answered") {
    return (
      <div className="round-answer" data-action={message.action}>
        <p className="round-label">{heading}</p>
        <AssistantMessage message={message} onOpen={onOpen} numbers={numbers} />
      </div>
    );
  }
  const split = splitClosingList(message.content);
  const kind = closingKind(split.title);
  return (
    <div className="round-answer" data-action={message.action}>
      <p className="round-label">{heading}</p>
      <AssistantMessage message={message} onOpen={onOpen} body={split.body} numbers={numbers} />
      {kind && (
        <section className="closing-panel" aria-labelledby={titleId}>
          <h3 id={titleId}>{t(kind === "vulnerabilities" ? "vulnerabilities" : "reinforce")}</h3>
          <AnswerView text={split.list} citations={message.citations} onOpen={onOpen} compact numbers={numbers} />
        </section>
      )}
    </div>
  );
}

function NewCase({
  rows,
  personas,
  scope,
  onScope,
  picker,
  remote,
  initialFacts = "",
  onCreate,
}: {
  rows: Parameters<typeof ScopePicker>[0]["rows"];
  personas: Parameters<typeof ScopePicker>[0]["personas"];
  scope: Scope;
  onScope: (s: Scope) => void;
  picker: React.ReactNode;
  remote: boolean; // ADR-024: the chosen model is external, so the facts leave the machine
  initialFacts?: string; // Brand.md 11.2: the facts written in the start page's box
  onCreate: (title: string, side: Side, facts: string, claim: string) => Promise<void>;
}) {
  const t = useTranslations("workbench.case");
  const ids = { title: useId(), side: useId(), facts: useId(), claim: useId() };
  const [title, setTitle] = useState("");
  const [side, setSide] = useState<Side>("actor");
  const [facts, setFacts] = useState(initialFacts);
  const [claim, setClaim] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="welcome case-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!title.trim() || !facts.trim() || busy) return;
        setBusy(true);
        await onCreate(title.trim(), side, facts.trim(), claim.trim());
        setBusy(false);
      }}
    >
      <h2>{t("newTitle")}</h2>
      <p>{t("newBody")}</p>
      <p>
        <button
          type="button"
          className="button button-quiet"
          onClick={() => {
            setTitle(t("example.title"));
            setSide("actor");
            setFacts(t("example.facts"));
            setClaim(t("example.claim"));
          }}
        >
          {t("example.load")}
        </button>
      </p>
      <div className="field">
        <label htmlFor={ids.title}>{t("title")}</label>
        <input id={ids.title} value={title} maxLength={TITLE_MAX} required placeholder={t("titlePlaceholder")} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.side}>{t("sideLabel")}</label>
        <select id={ids.side} value={side} onChange={(e) => setSide(e.target.value as Side)}>
          {SIDES.map((s) => (
            <option key={s} value={s}>
              {t(`side.${s}`)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={ids.facts}>{t("facts")}</label>
        <textarea id={ids.facts} value={facts} maxLength={CASE_MAX} rows={6} required placeholder={t("factsPlaceholder")} onChange={(e) => setFacts(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.claim}>{t("claim")}</label>
        <textarea id={ids.claim} value={claim} maxLength={CASE_MAX} rows={3} placeholder={t("claimPlaceholder")} onChange={(e) => setClaim(e.target.value)} />
      </div>
      <ScopePicker rows={rows} personas={personas} value={scope} onChange={onScope} />
      {picker}
      <p className="small muted">{t(remote ? "privacyNoteRemote" : "privacyNote")}</p>
      <button type="submit" className="button" disabled={busy || !title.trim() || !facts.trim() || !resolve(rows, scope.selection)}>
        {t("create")}
      </button>
    </form>
  );
}

/** Argumentar (ADR-016): a case, then rounds. Each round the user writes an argument and asks
 *  for the opposing counsel's attack or for the argument strengthened. */
export function ArgumentarView() {
  const t = useTranslations("workbench.case");
  const tw = useTranslations("workbench");
  const locale = useLocale();
  const { catalogs, failed } = useCatalogs();
  const w = useConversation("case");
  const [scope, setScope] = useState<Scope>(DEFAULT_SCOPE);
  const matters = useMatters();
  const [matter, setMatter] = useInitialMatter();
  const models = useModels();
  const [model, setModel] = useState<ModelKey | null>(null);
  const [draftFacts, setDraftFacts] = useState<{ text: string; n: number } | null>(null);
  useEffect(() => {
    // Brand.md 11.2: facts written on the start page prefill the case form (the side is chosen here)
    const d = takeDraft("case");
    if (!d) return;
    setDraftFacts({ text: d.text, n: d.at });
    setMatter(d.matter);
    setModel(d.model);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on arrival
  }, []);
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

  const sources = conversationSources(w.messages);
  const numbers = sourceNumbers(sources);
  return (
    <Workbench
      title={t("pageTitle")}
      failed={failed}
      ready={Boolean(catalogs)}
      history={<History kind="case" version={w.listVersion} activeId={w.activeId} onOpen={(id) => void openAndFocus(id)} onNew={() => { void w.open(null); setSeed({ text: "", n: Date.now() }); }} />}
      aside={w.conversation ? <SourcesPanel sources={sources} onOpen={setViewing} /> : undefined}
    >
      {catalogs && !w.conversation && (
        <>
          <NewCase
            key={draftFacts?.n ?? 0}
            initialFacts={draftFacts?.text}
            picker={
              <>
                <MatterPicker matters={matters} value={matter} onChange={setMatter} />
                <ModelPicker info={models} value={model} onChange={setModel} />
              </>
            }
            remote={isRemote(models, model)}
            rows={catalogs.rows}
            personas={catalogs.personas}
            scope={scope}
            onScope={setScope}
            onCreate={async (title, side, facts, claim) => {
              const j = resolve(catalogs.rows, scope.selection);
              if (!j) return;
              await w.create({
                kind: "case", title, jurisdiction_id: j.id, persona: scope.persona,
                language: locale === "en-US" ? "en" : "es", include_historical: scope.historical, matter_id: matter,
                case: { side, facts, claim },
                ...(model ? { model_profile: model } : {}),
              });
            }}
          />
          {w.error && <ErrorNote code={w.error} />}
        </>
      )}
      {catalogs && w.conversation && (
        <>
          <ConversationBar conversation={w.conversation} onRename={(x) => void w.rename(x)} onDelete={() => void w.remove()} matter={<MatterPicker compact matters={matters} value={w.conversation.matter_id} onChange={(m) => void w.move(m)} />} />
          <ScopeSummary conversation={w.conversation} rows={catalogs.rows} personas={catalogs.personas} />
          <CaseCard conversation={w.conversation} />
          <ol className="thread" aria-label={t("roundsLabel")}>
            {w.messages.map((m) => (
              <li key={m.seq} className={m.role === "user" ? "turn turn-user" : "turn turn-assistant"}>
                {m.role === "user" ? <UserMessage text={m.content} label={t("yourArgument")} /> : <RoundAnswer message={m} onOpen={setViewing} numbers={numbers} />}
              </li>
            ))}
            {w.runningHere && (
              <li className="turn turn-user">
                <UserMessage text={w.runningHere.pending} label={t("yourArgument")} />
              </li>
            )}
          </ol>
          {w.runningHere && <RunningNote running={w.runningHere} />}
          {w.busyElsewhere && <BusyElsewhere />}
          <Announcer what={w.announce} />
          {w.error && <ErrorNote code={w.error} />}
          <Composer
            label={t("composerLabel")}
            placeholder={t("composerPlaceholder")}
            rows={4}
            buttons={[
              { action: "attack", label: t("attack") },
              { action: "strengthen", label: t("strengthen"), primary: false },
            ]}
            disabled={Boolean(w.running)}
            seed={seed}
            onSubmit={(text, action) => w.send(text, action)}
          />
          <p className="small muted">{tw("slowNote")}</p>
        </>
      )}
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </Workbench>
  );
}
