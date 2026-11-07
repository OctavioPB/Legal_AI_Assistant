// Typed client for the same-origin gateway (/api/v1/*). The token never reaches this code.
import type { Jurisdiction } from "./jurisdiction";

export interface Citation {
  cite_id: string;
  citation_string: string;
}

export interface ChatResult {
  request_id: string;
  status: "answered" | "refused";
  answer: string | null;
  refusal_block: string | null;
  refusal_reason: string | null;
  citations: Citation[];
}

export interface Source {
  cite_id: string;
  citation_string: string;
  breadcrumb: string;
  text: string;
  article_text: string;
  article_label: string;
  is_current: boolean;
  is_public: boolean;
  document_title: string;
  version_label: string | null;
  valid_to: string | null;
}

export interface Persona {
  code: string;
  label_es: string;
  label_en: string;
}

export interface User {
  email: string;
  display_name: string;
  role: string;
  preferred_language: string;
}

export interface ChatBody {
  query: string;
  jurisdiction_id: number;
  persona: string;
  workflow: string;
  language: "es" | "en";
  include_historical: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`${status} ${code}`);
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1/${path}`, { ...init, headers: { "content-type": "application/json" } });
  if (!res.ok) {
    const body: { code?: string } = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.code ?? "error");
  }
  return (await res.json()) as T;
}

export const getMe = () => json<User>("me");
export const getJurisdictions = () => json<Jurisdiction[]>("catalog/jurisdictions");
export const getPersonas = () => json<Persona[]>("catalog/personas");
export const getSource = (citeId: string) => json<Source>(`sources/${encodeURIComponent(citeId)}`);

export interface SseEvent {
  event: string;
  data: unknown;
}

/** Parses an SSE byte stream into events (fetch + ReadableStream: EventSource cannot POST). */
export async function* readSse(res: Response): AsyncGenerator<SseEvent> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const event = /^event: (.*)$/m.exec(raw)?.[1];
      const data = /^data: (.*)$/m.exec(raw)?.[1];
      if (event && data !== undefined) yield { event, data: JSON.parse(data) as unknown };
    }
  }
}

export async function chatStream(body: ChatBody, onStage: (stage: string) => void): Promise<ChatResult> {
  const res = await fetch("/api/v1/chat/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err: { code?: string } = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.code ?? "error");
  }
  for await (const ev of readSse(res)) {
    if (ev.event === "stage") onStage(String(ev.data));
    else if (ev.event === "result") return ev.data as ChatResult;
    else if (ev.event === "error") throw new ApiError(500, (ev.data as { code?: string }).code ?? "error");
  }
  throw new ApiError(502, "stream_ended");
}
