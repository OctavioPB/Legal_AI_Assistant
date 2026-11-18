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
  reform: Succession | null; // ADR-017: a reform changed this unit after it was cited
}

export type ChangeKind = "unchanged" | "amended" | "derogated" | "removed" | "added";

export interface Succession {
  old: string;
  current: string | null;
  change: ChangeKind;
  published_on: string | null;
  citation_string: string;
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

// ---- conversations (ADR-016) ----------------------------------------------------------------
export type Kind = "chat" | "research" | "case";
export type Action = "ask" | "research" | "attack" | "strengthen";
export type Side = "actor" | "demandado" | "defensa" | "acusacion" | "tercero";
export const SIDES: Side[] = ["actor", "demandado", "defensa", "acusacion", "tercero"];

export interface CaseBrief {
  side: Side;
  facts: string;
  claim: string;
}

export interface Conversation {
  id: string;
  kind: Kind;
  title: string;
  language: "es" | "en";
  jurisdiction_id: number;
  persona: string;
  include_historical: boolean;
  case: CaseBrief | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  matter_id: string | null;
  mine: boolean;
  model_profile: ModelKey;
}

// ---- model profiles (ADR-024) ---------------------------------------------------------------
export type ModelKey = "gemma" | "qwen" | "claude";

export interface Measurement {
  run: string;
  measured_on: string;
  cases: number;
  reviewed_by_lawyer: boolean;
  recall_at_8: number;
  citation_precision: number;
  jurisdiction_leakage: number;
  refusal_accuracy: number;
  false_refusal: number;
  total_p50_s: number;
}

export interface ModelOption {
  key: ModelKey;
  locality: "local" | "remote";
  model_id: string | null;
  available: boolean;
  unavailable_reason: string | null;
  allowed: boolean;
  default: boolean;
  measurement: Measurement | null;
}

export interface ModelsInfo {
  options: ModelOption[];
  allow_remote: boolean;
  can_manage: boolean;
}

export const getModels = () => json<ModelsInfo>("models");
export const setRemotePolicy = async (allow: boolean) =>
  (await (await send("models/policy", "POST", { allow_remote: allow })).json()) as ModelsInfo;

export interface Message {
  seq: number;
  role: "user" | "assistant";
  action: Action;
  content: string;
  status: "answered" | "refused" | null;
  refusal_reason: string | null;
  citations: Citation[];
  request_id: string | null;
  created_at: string;
}

export interface NewConversation {
  kind: Kind;
  title: string;
  jurisdiction_id: number;
  persona: string;
  language: "es" | "en";
  include_historical: boolean;
  case?: CaseBrief;
  matter_id?: string | null;
  model_profile?: ModelKey;
}

async function send(path: string, method: "POST" | "PATCH" | "DELETE", body?: unknown): Promise<Response> {
  const res = await fetch(`/api/v1/${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const err: { code?: string } = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.code ?? "error");
  }
  return res;
}

export const listConversations = (kind: Kind, q = "") =>
  json<Conversation[]>(`conversations?kind=${kind}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`);
export const getConversation = (id: string) =>
  json<{ conversation: Conversation; messages: Message[] }>(`conversations/${encodeURIComponent(id)}`);
export const createConversation = async (body: NewConversation) =>
  (await (await send("conversations", "POST", body)).json()) as Conversation;
export const renameConversation = async (id: string, title: string) =>
  (await (await send(`conversations/${encodeURIComponent(id)}`, "PATCH", { title })).json()) as Conversation;
export const moveConversation = async (id: string, matterId: string | null) =>
  (await (await send(`conversations/${encodeURIComponent(id)}`, "PATCH", { move: true, matter_id: matterId })).json()) as Conversation;
export const listMatterConversations = (matterId: string) =>
  json<Conversation[]>(`conversations?matter=${encodeURIComponent(matterId)}`);
export const deleteConversation = async (id: string) => {
  await send(`conversations/${encodeURIComponent(id)}`, "DELETE");
};

/** One turn: stages while it runs, then the two stored messages (the user's and the answer). */
export async function sendMessage(
  id: string,
  text: string,
  action: Action,
  onStage: (stage: string) => void,
): Promise<{ result: ChatResult; messages: Message[] }> {
  const res = await send(`conversations/${encodeURIComponent(id)}/messages/stream`, "POST", { text, action });
  for await (const ev of readSse(res)) {
    if (ev.event === "stage") onStage(String(ev.data));
    else if (ev.event === "result") return ev.data as { result: ChatResult; messages: Message[] };
    else if (ev.event === "error") throw new ApiError(500, (ev.data as { code?: string }).code ?? "error");
  }
  throw new ApiError(502, "stream_ended");
}

// ---- 1. law navigator --------------------------------------------------------------------------
export interface Law {
  official_id: string;
  title: string;
  doc_type: string;
  language: "es" | "en";
  version_label: string | null;
  valid_from: string | null;
  jurisdiction_id: number;
  jurisdiction_es: string;
  jurisdiction_en: string;
  last_reform: string | null;
}

export interface TocNode {
  path: string;
  node_type: string;
  label: string;
  heading: string;
  children: TocNode[];
}

export interface Citable {
  cite_id: string;
  citation_string: string;
  label: string;
  text: string;
  is_current: boolean;
}

export interface NodeReform {
  cite_id: string;
  previous_cite_id: string | null;
  change: ChangeKind;
  published_on: string | null;
  previous_version: string | null;
}

export interface NodeView {
  official_id: string;
  law_title: string;
  version_label: string | null;
  path: string;
  node_type: string;
  label: string;
  text: string;
  trail: { path: string; label: string }[];
  children: { path: string; node_type: string; label: string }[];
  citables: Citable[];
  prev_path: string | null;
  next_path: string | null;
  reforms: NodeReform[];
}

export interface LawVersion {
  document_id: string;
  version_label: string | null;
  valid_from: string | null;
  valid_to: string | null;
  current: boolean;
  text: string | null;
}

const lawPath = (id: string) => `laws/${encodeURIComponent(id)}`;
export const listLaws = () => json<Law[]>("laws");
export const getToc = (id: string) => json<TocNode[]>(`${lawPath(id)}/toc`);
export const findArticle = (id: string, ref: string) =>
  json<{ path: string; label: string }>(`${lawPath(id)}/find?ref=${encodeURIComponent(ref)}`);
export const getNode = (id: string, path: string) =>
  json<NodeView>(`${lawPath(id)}/node?path=${encodeURIComponent(path)}`);
export const getHistory = (id: string, path: string) =>
  json<LawVersion[]>(`${lawPath(id)}/history?path=${encodeURIComponent(path)}`);

// ---- 5. matters (ADR-018) ----------------------------------------------------------------------
export interface Matter {
  id: string;
  title: string;
  client_ref: string;
  description: string;
  status: "open" | "closed";
  role: "owner" | "member";
  members: number;
  items: number;
  created_at: string;
  updated_at: string;
}

export interface MatterSource {
  id: string;
  cite_id: string;
  citation_string: string;
  note: string;
  added_by: string;
  added_at: string;
  change: ChangeKind | null;
  current_cite_id: string | null;
}

export interface MatterDetail {
  matter: Matter;
  members: { user_id: string; display_name: string; email: string; role: "owner" | "member" }[];
  notes: { id: string; body: string; author: string; mine: boolean; created_at: string }[];
  sources: MatterSource[];
  reports: { id: string; title: string; summary: Record<string, number>; created_at: string }[];
}

export interface Colleague {
  user_id: string;
  display_name: string;
  email: string;
}

const matterPath = (id: string) => `matters/${encodeURIComponent(id)}`;
export const listMatters = () => json<Matter[]>("matters");
export const getMatter = (id: string) => json<MatterDetail>(matterPath(id));
export const listColleagues = () => json<Colleague[]>("matters/colleagues");
export const createMatter = async (body: { title: string; client_ref: string; description: string }) =>
  (await (await send("matters", "POST", body)).json()) as Matter;
export const updateMatter = async (
  id: string,
  body: Partial<Pick<Matter, "title" | "client_ref" | "description" | "status">>,
) => (await (await send(matterPath(id), "PATCH", body)).json()) as Matter;
export const deleteMatter = async (id: string) => {
  await send(matterPath(id), "DELETE");
};
export const addMember = async (id: string, userId: string) => {
  await send(`${matterPath(id)}/members`, "POST", { user_id: userId });
};
export const removeMember = async (id: string, userId: string) => {
  await send(`${matterPath(id)}/members/${encodeURIComponent(userId)}`, "DELETE");
};
export const addNote = async (id: string, body: string) => {
  await send(`${matterPath(id)}/notes`, "POST", { body });
};
export const deleteNote = async (id: string, noteId: string) => {
  await send(`${matterPath(id)}/notes/${encodeURIComponent(noteId)}`, "DELETE");
};
export const saveSource = async (id: string, citeId: string, note = "") => {
  await send(`${matterPath(id)}/sources`, "POST", { cite_id: citeId, note });
};
export const deleteSource = async (id: string, sourceId: string) => {
  await send(`${matterPath(id)}/sources/${encodeURIComponent(sourceId)}`, "DELETE");
};

// ---- 2. brief verifier (ADR-019) ---------------------------------------------------------------
export type FindingStatus =
  | "vigente"
  | "derogado"
  | "no_vigente"
  | "inexistente"
  | "fuera_del_corpus"
  | "ley_no_identificada"
  | "ambigua";
export type QuoteStatus = "coincide" | "no_coincide" | "coincide_version_anterior" | "corta";
export const FINDING_STATUSES: FindingStatus[] = [
  "vigente",
  "derogado",
  "no_vigente",
  "inexistente",
  "fuera_del_corpus",
  "ley_no_identificada",
  "ambigua",
];

export interface Finding {
  ref: string;
  official_id: string | null;
  status: FindingStatus;
  quote: QuoteStatus | null;
  cite_ids: string[];
  citation_string: string | null;
  reform: { change: ChangeKind; published_on: string } | null;
  span?: [number, number];
  inferred: boolean;
}

export interface VerifyResult {
  report_id: string;
  title: string;
  summary: Record<string, number>;
  findings: Finding[];
  text: string | null;
  warnings: string[];
}

export interface Report {
  id: string;
  title: string;
  source_kind: "text" | "pdf";
  matter_id: string | null;
  mine: boolean;
  summary: Record<string, number>;
  items: Finding[];
  created_at: string;
}

export const verifyText = async (title: string, text: string, matterId: string | null) =>
  (await (await send("verify", "POST", { title, text, matter_id: matterId })).json()) as VerifyResult;

export async function verifyPdf(title: string, file: Blob, matterId: string | null): Promise<VerifyResult> {
  const q = new URLSearchParams({ title });
  if (matterId) q.set("matter_id", matterId);
  const res = await fetch(`/api/v1/verify/pdf?${q.toString()}`, {
    method: "POST",
    headers: { "content-type": "application/pdf" },
    body: file,
  });
  if (!res.ok) {
    const err: { code?: string } = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.code ?? "error");
  }
  return (await res.json()) as VerifyResult;
}
export const listReports = () => json<Report[]>("verify/reports");
export const deleteReport = async (id: string) => {
  await send(`verify/reports/${encodeURIComponent(id)}`, "DELETE");
};

// ---- firm documents (ADR-025) ------------------------------------------------------------------
export const FILE_KINDS = ["contrato", "escrito", "resolucion", "norma", "otro"] as const;
export type FileKind = (typeof FILE_KINDS)[number];
export const MAX_FILE_BYTES = 25 * 1024 * 1024;

export interface ClientFile {
  id: string;
  title: string;
  kind: FileKind;
  language: "es" | "en";
  jurisdiction_id: number;
  matter_id: string | null;
  status: "processing" | "ready" | "failed";
  error_key: string | null;
  pages: number | null;
  chunk_count: number | null;
  source_format: "pdf" | "docx" | "text";
  created_at: string;
  mine: boolean;
  uploaded_by_name: string;
  can_delete: boolean;
}

export interface FileUnit {
  cite_id: string;
  citation_string: string;
  text: string;
}

export interface NewFile {
  title: string;
  kind: FileKind;
  language: "es" | "en";
  jurisdiction_id: number;
  matter_id: string | null;
}

export const listFiles = () => json<ClientFile[]>("files");
export const getFileText = (id: string) => json<FileUnit[]>(`files/${encodeURIComponent(id)}/text`);
export const deleteFile = async (id: string) => {
  await send(`files/${encodeURIComponent(id)}`, "DELETE");
};
/** The bytes go as `application/octet-stream`: the API recognises the format by content. */
export async function uploadFile(meta: NewFile, file: Blob): Promise<ClientFile> {
  const q = new URLSearchParams({ title: meta.title, kind: meta.kind, language: meta.language, jurisdiction_id: String(meta.jurisdiction_id) });
  if (meta.matter_id) q.set("matter_id", meta.matter_id);
  const res = await fetch(`/api/v1/files?${q.toString()}`, { method: "POST", headers: { "content-type": "application/octet-stream" }, body: file });
  if (!res.ok) {
    const err: { code?: string } = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.code ?? "error");
  }
  return (await res.json()) as ClientFile;
}

// ---- 6. reforms (ADR-017) ----------------------------------------------------------------------
export interface Alert {
  where: "conversation" | "matter_source" | "report";
  target_id: string;
  target_title: string;
  kind: Kind | null;
  matter_id: string | null;
  cite_id: string;
  citation_string: string;
  change: ChangeKind;
  published_on: string | null;
  current_cite_id: string | null;
}

export interface ReformChange {
  id: string;
  official_id: string;
  title: string;
  old_version: string | null;
  new_version: string | null;
  published_on: string | null;
  detected_at: string;
  counts: Record<string, number>;
}

export interface ReformOverview {
  changes: ReformChange[];
  checks: { official_id: string; title: string | null; checked_at: string; outcome: string; http_status: number | null; detail: string | null }[];
}

export interface LineageRow {
  change: ChangeKind;
  citation_string: string;
  old_cite_id: string | null;
  new_cite_id: string | null;
}

/** ADR-026: what an official index lists. Read only: an operator approves a law with
 *  `python -m app.ingestion.discover approve`, never from the web. */
export interface IndexEntry {
  index_id: string;
  entry_key: string;
  title: string;
  url: string;
  status: "available" | "new" | "approved" | "dismissed";
  published_on: string | null;
  last_reform_on: string | null;
  last_reform_text: string | null;
  note: string | null;
  first_seen_at: string;
  missing: boolean;
  loaded: boolean;
}

export interface Discovery {
  indexes: { index_id: string; title: string; url: string; checked_at: string | null; outcome: string | null; detail: string | null }[];
  entries: IndexEntry[];
}

export const listAlerts = () => json<Alert[]>("reforms/alerts");
export const getReforms = () => json<ReformOverview>("reforms");
export const getDiscovery = () => json<Discovery>("reforms/indexes");
export const getReformDetail = (id: string) => json<LineageRow[]>(`reforms/${encodeURIComponent(id)}`);

// ---- legal news (ADR-027) ------------------------------------------------------------------------
/** One item of the shared feed. Title and excerpt are the source's own words (never generated). */
export interface NewsItem {
  id: string;
  kind: "reform" | "new_law" | "recent_reform" | "court" | "press";
  title: string;
  url: string; // https:// (the source) or /reformas?c=... (inside the app)
  outlet: string;
  official: boolean;
  published_at: string | null; // null: the source gives no date (first_seen_at is shown)
  first_seen_at: string;
  excerpt: string | null;
  terms: string[];
  facts: { counts?: Record<string, number>; last_reform?: string | null; note?: string | null };
  score: number;
}

export interface NewsFeed {
  refreshed_at: string | null;
  items: NewsItem[];
  sources: { source_id: string; outlet: string; official: boolean; checked_at: string | null; outcome: string | null }[];
}

export const getNews = () => json<NewsFeed>("news");

// ---- key-points sheet of a matter (ADR-028) ------------------------------------------------------
export interface Cheatsheet {
  id: string;
  matter_id: string;
  created_at: string;
  created_by_name: string | null;
  language: "es" | "en";
  model_profile: string;
  status: "answered" | "refused";
  content: string | null; // validated by the system; legal points cite, reasoning lines are marked
  refusal_reason: string | null; // a catalog key, never model text
  citations: Citation[];
  material: Record<string, number>;
}

const sheetsPath = (matter: string) => `${matterPath(matter)}/cheatsheets`;
export const listCheatsheets = (matter: string) => json<Cheatsheet[]>(sheetsPath(matter));
export const getCheatsheet = (matter: string, id: string) => json<Cheatsheet>(`${sheetsPath(matter)}/${encodeURIComponent(id)}`);

export async function generateCheatsheet(
  matter: string,
  body: { language: "es" | "en"; model_profile?: string },
  onStage: (stage: string) => void,
): Promise<Cheatsheet> {
  const res = await send(`${sheetsPath(matter)}/stream`, "POST", body);
  for await (const ev of readSse(res)) {
    if (ev.event === "stage") onStage(String(ev.data));
    else if (ev.event === "result") return ev.data as Cheatsheet;
    else if (ev.event === "error") throw new ApiError(500, (ev.data as { code?: string }).code ?? "error");
  }
  throw new ApiError(502, "stream_ended");
}
