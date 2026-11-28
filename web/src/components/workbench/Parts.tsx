"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { getSource, type Action, type Citation, type Conversation, type Message } from "@/lib/api";
import { TITLE_MAX } from "@/lib/conversation";
import { errorKey, type Running } from "@/lib/useWorkbench";
import { AnswerView } from "../AnswerView";
import { RefusalCard } from "../RefusalCard";

/** Title of the open conversation with rename and delete (delete asks inline, no native dialog). */
export function ConversationBar({
  conversation,
  onRename,
  onDelete,
  matter,
}: {
  conversation: Conversation;
  onRename: (title: string) => void;
  onDelete: () => void;
  matter?: ReactNode; // the matter picker (author only, ADR-018)
}) {
  const t = useTranslations("workbench.bar");
  const inputId = useId();
  const [mode, setMode] = useState<"view" | "rename" | "delete">("view");
  const [title, setTitle] = useState(conversation.title);
  useEffect(() => {
    setMode("view");
    setTitle(conversation.title);
  }, [conversation.id, conversation.title]);

  if (mode === "rename") {
    return (
      <form
        className="conversation-bar"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) onRename(title.trim());
          setMode("view");
        }}
      >
        <label htmlFor={inputId} className="sr-only">
          {t("newTitle")}
        </label>
        <input id={inputId} className="bar-input" value={title} maxLength={TITLE_MAX} required onChange={(e) => setTitle(e.target.value)} />
        <button type="submit" className="button">
          {t("save")}
        </button>
        <button type="button" className="button button-quiet" onClick={() => setMode("view")}>
          {t("cancel")}
        </button>
      </form>
    );
  }
  return (
    <div className="conversation-bar">
      {/* tabIndex -1: opening a conversation from the history moves the focus here */}
      <h2 id="conversation-title" className="conversation-title" tabIndex={-1}>
        {conversation.title}
      </h2>
      {!conversation.mine ? (
        <p className="small muted">{t("shared")}</p>
      ) : mode === "delete" ? (
        <div className="bar-confirm" role="group" aria-label={t("confirmDelete")}>
          <span>{t("confirmDelete")}</span>
          <button type="button" className="button" onClick={onDelete}>
            {t("delete")}
          </button>
          <button type="button" className="button button-quiet" onClick={() => setMode("view")}>
            {t("cancel")}
          </button>
        </div>
      ) : (
        <div className="bar-actions">
          <button type="button" className="button button-quiet" onClick={() => setMode("rename")}>
            {t("rename")}
          </button>
          <button type="button" className="button button-quiet" onClick={() => setMode("delete")}>
            {t("delete")}
          </button>
          {matter}
        </div>
      )}
    </div>
  );
}

/** One stored answer: the validated text, or the system's refusal (never the model's words). */
export function AssistantMessage({
  message,
  onOpen,
  body,
  numbers,
}: {
  message: Message;
  onOpen: (id: string) => void;
  body?: string;
  numbers?: Map<string, number>;
}) {
  if (message.status === "refused") return <RefusalCard reason={message.refusal_reason} />;
  return <AnswerView text={body ?? message.content} citations={message.citations} onOpen={onOpen} compact numbers={numbers} />;
}

export function UserMessage({ text, label }: { text: string; label: string }) {
  return (
    <div className="msg-user">
      <p className="msg-label">{label}</p>
      <p className="msg-text">{text}</p>
    </div>
  );
}

/** While a turn runs: the stage (announced once per change) and the elapsed time (not announced).
 *  Brand.md 4: a visible state, never an infinite spinner. */
export function RunningNote({ running }: { running: Running }) {
  const t = useTranslations("workspace.stage");
  const tw = useTranslations("workbench");
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const secs = Math.max(0, Math.floor((now - running.startedAt) / 1000));
  const clock = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
  return (
    <div className="running">
      <p role="status">{t(running.stage === "generating" ? "generating" : "retrieving")}</p>
      <p className="small muted" aria-hidden="true">
        {tw("elapsed", { clock })}
      </p>
      <p className="small muted">{tw("slowNote")}</p>
    </div>
  );
}

export function ErrorNote({ code }: { code: string }) {
  const t = useTranslations("workspace.error");
  return (
    <p className="form-status" role="alert">
      {t(errorKey(code))}
    </p>
  );
}

// The excerpt of each card, fetched once per page view (the text of a cite_id never changes, inv. 3).
const excerpts = new Map<string, Promise<string>>();
function excerptOf(citeId: string): Promise<string> {
  let p = excerpts.get(citeId);
  if (!p) {
    p = getSource(citeId).then((s) => s.text);
    p.catch(() => excerpts.delete(citeId));
    excerpts.set(citeId, p);
  }
  return p;
}

function SourceCard({ source, n, onOpen }: { source: Citation; n: number; onOpen: (id: string) => void }) {
  const t = useTranslations("workbench.sources");
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    excerptOf(source.cite_id)
      .then((x) => live && setText(x))
      .catch(() => live && setText(""));
    return () => {
      live = false;
    };
  }, [source.cite_id]);
  return (
    <li className="source-card" id={`source-${n}`}>
      <p className="source-card-head">
        <span className="source-num" aria-hidden="true">
          {n}
        </span>
        <span className="source-card-title">{source.citation_string}</span>
      </p>
      {source.regime_label && !source.applicability && <p className="small muted">{t("regime", { name: source.regime_label })}</p>}
      {source.applicability && (
        <p className="reform-notice small" role="note">
          <span aria-hidden="true">⚠ </span>
          {t(`applicability.${source.applicability}`, { name: source.regime_label ?? "" })}
        </p>
      )}
      {text === null ? (
        <p className="small muted">{t("loading")}</p>
      ) : (
        text && (
          <p className="source-excerpt legal-text" lang={source.cite_id.startsWith("US-") ? "en" : "es"}>
            {text}
          </p>
        )
      )}
      <button type="button" className="source-open" onClick={() => onOpen(source.cite_id)} title={source.cite_id}>
        {t("open")}
        <span className="sr-only">: {source.citation_string}</span>
      </button>
    </li>
  );
}

/** Every source cited in the conversation (Brand.md 11.3): numbered cards in order of first
 *  appearance (the same numbers as in the answers), with an excerpt; each opens the viewer. */
export function SourcesPanel({ sources, onOpen }: { sources: Citation[]; onOpen: (id: string) => void }) {
  const t = useTranslations("workbench.sources");
  const titleId = useId();
  return (
    <aside className="sources-panel" aria-labelledby={titleId}>
      <h2 id={titleId} className="sources-title">
        {t("title")}
      </h2>
      {sources.length === 0 ? (
        <p className="small muted">{t("empty")}</p>
      ) : (
        <ol className="source-cards">
          {sources.map((c, i) => (
            <SourceCard key={c.cite_id} source={c} n={i + 1} onOpen={onOpen} />
          ))}
        </ol>
      )}
    </aside>
  );
}

/** Announces, once, that the answer arrived (minutes after asking; Brand.md 6, 8.3). */
export function Announcer({ what }: { what: "answerReady" | "refusalReady" | null }) {
  const t = useTranslations("workbench");
  return (
    <p className="sr-only" role="status">
      {what ? t(what) : ""}
    </p>
  );
}

/** One model, one answer at a time: say why the composer is waiting instead of a dead button. */
export function BusyElsewhere() {
  const t = useTranslations("workbench");
  return <p className="small muted">{t("busyElsewhere")}</p>;
}

/** Ready-made examples: one click puts the text in the composer (nothing is sent by itself). */
export function Examples({ items, onPick }: { items: string[]; onPick: (text: string) => void }) {
  const t = useTranslations("workbench");
  const id = useId();
  return (
    <div className="examples">
      <p id={id} className="small muted">
        {t("examplesLabel")}
      </p>
      <ul className="examples-list" aria-labelledby={id}>
        {items.map((e) => (
          <li key={e}>
            <button type="button" className="example" onClick={() => onPick(e)}>
              {e}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface Seed {
  text: string;
  n: number; // bump to apply the same text again
}

export interface ComposerButton {
  action: Action;
  label: string;
  primary?: boolean;
}

/** Text area + one or more submit actions. The text stays if the turn fails. */
export function Composer({
  label,
  placeholder,
  buttons,
  disabled,
  onSubmit,
  max = 4000,
  rows = 3,
  seed,
}: {
  label: string;
  placeholder: string;
  buttons: ComposerButton[];
  disabled: boolean;
  onSubmit: (text: string, action: Action) => Promise<boolean>;
  max?: number;
  rows?: number;
  seed?: Seed;
}) {
  const t = useTranslations("workspace.query");
  const tw = useTranslations("workbench");
  const id = useId();
  const hintId = useId();
  const area = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  useEffect(() => {
    if (!seed) return;
    setText(seed.text);
    area.current?.focus();
  }, [seed]);
  const submit = async (action: Action) => {
    const value = text.trim();
    if (!value || disabled) return;
    setText("");
    const ok = await onSubmit(value, action);
    if (!ok) setText(value);
  };
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void submit(buttons[0].action);
      }}
    >
      <label htmlFor={id} className="composer-label">
        {label}
      </label>
      <textarea
        id={id}
        ref={area}
        aria-describedby={hintId}
        value={text}
        maxLength={max}
        rows={rows}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            void submit(buttons[0].action);
          }
        }}
      />
      <div className="composer-row">
        <span id={hintId} className="small muted">
          {t("count", { count: text.length, max })} · {tw("shortcut")}
        </span>
        <span className="composer-actions">
          {buttons.map((b, i) => (
            <button
              key={b.action}
              type={i === 0 ? "submit" : "button"}
              className={b.primary === false ? "button button-quiet" : "button"}
              disabled={disabled || !text.trim()}
              onClick={i === 0 ? undefined : () => void submit(b.action)}
            >
              {b.label}
            </button>
          ))}
        </span>
      </div>
    </form>
  );
}
