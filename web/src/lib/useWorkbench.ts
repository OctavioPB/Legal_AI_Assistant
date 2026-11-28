"use client";
// State shared by the three workspaces (ADR-016): catalogs, the selected conversation (kept in
// the URL as ?c=<id>, so a reload or a shared link reopens it), the running turn and its errors.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createConversation,
  deleteConversation,
  moveConversation,
  getConversation,
  getJurisdictions,
  getMe,
  getPersonas,
  renameConversation,
  sendMessage,
  type Action,
  type Conversation,
  type Kind,
  type Message,
  type NewConversation,
  type Persona,
  type User,
} from "./api";
import type { Jurisdiction } from "./jurisdiction";

export interface Catalogs {
  rows: Jurisdiction[];
  personas: Persona[];
  user: User;
}

function toLogin() {
  window.location.assign("/login");
}

/** The closed catalogs every workspace needs. A 401 anywhere means the session ended. */
export function useCatalogs(): { catalogs: Catalogs | null; failed: boolean } {
  const [catalogs, setCatalogs] = useState<Catalogs | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    Promise.all([getJurisdictions(), getPersonas(), getMe()])
      .then(([rows, personas, user]) => setCatalogs({ rows, personas, user }))
      .catch((e: unknown) => (e instanceof ApiError && e.status === 401 ? toLogin() : setFailed(true)));
  }, []);
  return { catalogs, failed };
}

export interface Running {
  stage: string;
  startedAt: number;
  pending: string; // the user's text, shown while the turn runs
  action: Action;
  convId: string | null; // null until a first message has created its conversation
}

function readParam(): string | null {
  try {
    return new URLSearchParams(window.location.search).get("c");
  } catch {
    return null;
  }
}

function writeParam(id: string | null) {
  // only `c` is ours: `?matter=` (preselected matter, ADR-018) must survive the first render
  const q = new URLSearchParams(window.location.search);
  if (id) q.set("c", id);
  else q.delete("c");
  const s = q.toString();
  const next = s ? `${window.location.pathname}?${s}` : window.location.pathname;
  // only when it changes: a no-op replaceState is still a navigation event to the browser (it made the
  // "no reload" browser check flaky whenever the mount-time call landed after the check started)
  if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", next);
}

const ERROR_KEYS = ["rate_limited", "llm_unavailable", "model_not_allowed", "model_unavailable", "model_declined", "cites_matter_files"] as const;
export type ErrorKey = (typeof ERROR_KEYS)[number] | "generic";

export function errorKey(code: string): ErrorKey {
  return (ERROR_KEYS as readonly string[]).includes(code) ? (code as ErrorKey) : "generic";
}

export function useConversation(kind: Kind) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [running, setRunning] = useState<Running | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [listVersion, setListVersion] = useState(0);
  // What a screen reader hears when a turn ends (a catalog key; the stage itself is role=status).
  const [announce, setAnnounce] = useState<"answerReady" | "refusalReady" | null>(null);
  const loadSeq = useRef(0);
  const activeRef = useRef<string | null>(null); // the conversation on screen, read inside callbacks

  const fail = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) return toLogin();
    setError(e instanceof ApiError ? e.code : "generic");
  }, []);

  const open = useCallback(
    async (id: string | null) => {
      const seq = ++loadSeq.current;
      setError(null);
      setActiveId(id);
      activeRef.current = id;
      writeParam(id);
      if (!id) {
        setConversation(null);
        setMessages([]);
        return;
      }
      try {
        const detail = await getConversation(id);
        if (seq !== loadSeq.current) return; // a newer selection won
        if (detail.conversation.kind !== kind) throw new ApiError(404, "not_found");
        setConversation(detail.conversation);
        setMessages(detail.messages);
      } catch (e) {
        if (seq !== loadSeq.current) return;
        setConversation(null);
        setMessages([]);
        setActiveId(null);
        activeRef.current = null;
        writeParam(null);
        fail(e);
      }
    },
    [fail, kind],
  );

  useEffect(() => {
    void open(readParam());
  }, [open]);

  /** Runs one turn. Creates the conversation first when `create` is given (first message). */
  const send = useCallback(
    async (text: string, action: Action, create?: NewConversation): Promise<boolean> => {
      if (running) return false;
      setError(null);
      setAnnounce(null);
      setRunning({ stage: "retrieving", startedAt: Date.now(), pending: text, action, convId: conversation?.id ?? null });
      try {
        let conv = conversation;
        if (!conv) {
          if (!create) throw new ApiError(400, "generic");
          conv = await createConversation(create);
          setConversation(conv);
          setActiveId(conv.id);
          activeRef.current = conv.id;
          writeParam(conv.id);
          const created = conv.id;
          setRunning((r) => (r ? { ...r, convId: created } : r));
          setMessages([]);
          setListVersion((v) => v + 1);
        }
        const out = await sendMessage(conv.id, text, action, (stage) =>
          setRunning((r) => (r ? { ...r, stage } : r)),
        );
        // The user may have opened another conversation while this one was answering: the
        // answer is stored with ITS conversation and must never be appended to the one on screen.
        if (activeRef.current === conv.id) {
          setMessages((m) => [...m, ...out.messages]);
          setAnnounce(out.result.status === "answered" ? "answerReady" : "refusalReady");
        }
        setListVersion((v) => v + 1);
        return true;
      } catch (e) {
        fail(e);
        return false;
      } finally {
        setRunning(null);
      }
    },
    [conversation, fail, running],
  );

  const create = useCallback(
    async (body: NewConversation) => {
      setError(null);
      try {
        const conv = await createConversation(body);
        setListVersion((v) => v + 1);
        await open(conv.id);
        return conv;
      } catch (e) {
        fail(e);
        return null;
      }
    },
    [fail, open],
  );

  const rename = useCallback(
    async (title: string) => {
      if (!conversation) return;
      try {
        setConversation(await renameConversation(conversation.id, title));
        setListVersion((v) => v + 1);
      } catch (e) {
        fail(e);
      }
    },
    [conversation, fail],
  );

  const move = useCallback(
    async (matterId: string | null) => {
      if (!conversation) return;
      try {
        setConversation(await moveConversation(conversation.id, matterId));
        setListVersion((v) => v + 1);
      } catch (e) {
        fail(e);
      }
    },
    [conversation, fail],
  );

  const remove = useCallback(async () => {
    if (!conversation) return;
    try {
      await deleteConversation(conversation.id);
      setListVersion((v) => v + 1);
      await open(null);
    } catch (e) {
      fail(e);
    }
  }, [conversation, fail, open]);

  // The turn in progress belongs to ONE conversation: elsewhere it only blocks a second turn
  // (the local model answers one request at a time) and says so.
  const runningHere = running && running.convId === activeId ? running : null;
  const busyElsewhere = Boolean(running) && !runningHere;
  return { activeId, conversation, messages, running, runningHere, busyElsewhere, error, listVersion, announce, open, send, create, rename, move, remove };
}
