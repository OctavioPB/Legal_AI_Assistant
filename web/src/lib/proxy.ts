// The browser never talks to the API directly and never sees the token: a same-origin route handler
// adds the bearer from the httpOnly cookie. Only these exact routes are forwarded (an open proxy
// would let the page reach any internal path), and only the query parameters each route declares.

type Param = (value: string) => string | null;

export interface Route {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  pattern: RegExp;
  stream?: boolean;
  query?: Record<string, Param>; // the query parameters forwarded (only where the API reads them)
  body?: BodyKind; // request body type (default json); binary bodies have their own size cap
}

export type BodyKind = "json" | "pdf" | "file";
export const BODY_TYPE: Record<BodyKind, string> = {
  json: "application/json",
  pdf: "application/pdf",
  file: "application/octet-stream", // ADR-025: a PDF, .docx or text file; the API reads the content
};

const UUID_RE = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const LAW = "[A-Za-z0-9_]{1,64}";

const oneOf =
  (...values: string[]): Param =>
  (v) =>
    values.includes(v) ? v : null;
const uuid: Param = (v) => (new RegExp(`^${UUID_RE}$`).test(v) ? v : null);
const text =
  (max: number): Param =>
  (v) =>
    v ? v.slice(0, max) : null;
const digits: Param = (v) => (/^[1-9][0-9]{0,8}$/.test(v) ? v : null);
const ltree: Param = (v) => (/^[a-z0-9_]+(\.[a-z0-9_]+){0,15}$/.test(v) && v.length <= 300 ? v : null);

const r = (method: Route["method"], path: string, extra: Partial<Route> = {}): Route => ({
  method,
  pattern: new RegExp(`^${path}$`),
  ...extra,
});

export const ALLOWED: Route[] = [
  r("GET", "me"),
  r("GET", "catalog/(jurisdictions|personas)"),
  // `POST chat` and `chat/stream` (single questions outside a conversation) stay API-only: the UI
  // asks through conversations, so the gateway does not forward them.
  r("GET", "sources/[A-Za-z0-9][A-Za-z0-9._-]{0,199}"),
  // ADR-016/018: conversations, optionally inside a matter
  r("GET", "conversations", { query: { kind: oneOf("chat", "research", "case"), q: text(200), matter: uuid } }),
  r("POST", "conversations"),
  r("GET", `conversations/${UUID_RE}`),
  r("PATCH", `conversations/${UUID_RE}`),
  r("DELETE", `conversations/${UUID_RE}`),
  r("POST", `conversations/${UUID_RE}/messages/stream`, { stream: true }),
  // feature 1: the law navigator (read-only)
  r("GET", "laws"),
  r("GET", `laws/${LAW}/toc`),
  r("GET", `laws/${LAW}/find`, { query: { ref: text(40) } }),
  r("GET", `laws/${LAW}/(node|history)`, { query: { path: ltree } }),
  // feature 5 (ADR-018): matters
  r("GET", "matters"),
  r("POST", "matters"),
  r("GET", "matters/colleagues"),
  r("GET", `matters/${UUID_RE}`),
  r("PATCH", `matters/${UUID_RE}`),
  r("DELETE", `matters/${UUID_RE}`),
  r("POST", `matters/${UUID_RE}/(members|notes|sources)`),
  r("DELETE", `matters/${UUID_RE}/(members|notes|sources)/${UUID_RE}`),
  // ADR-028: the key-points sheet of a matter (members only, enforced by RLS)
  r("GET", `matters/${UUID_RE}/cheatsheets`),
  r("GET", `matters/${UUID_RE}/cheatsheets/${UUID_RE}`),
  r("POST", `matters/${UUID_RE}/cheatsheets/stream`, { stream: true }),
  // feature 2 (ADR-019): the brief verifier
  r("POST", "verify"),
  r("POST", "verify/pdf", { body: "pdf", query: { title: text(200), matter_id: uuid } }),
  r("GET", "verify/reports"),
  r("DELETE", `verify/reports/${UUID_RE}`),
  // feature 6 (ADR-017): reforms and alerts
  r("GET", "reforms"),
  r("GET", "reforms/alerts"),
  r("GET", "reforms/indexes"), // ADR-026: read only (approval is an operator's command)
  r("GET", "news"), // ADR-027: the shared legal news feed, read only
  r("GET", `reforms/${UUID_RE}`),
  // ADR-025: the firm's own documents
  r("GET", "files", { query: { matter: uuid } }),
  r("POST", "files", {
    body: "file",
    query: {
      title: text(200),
      kind: oneOf("contrato", "escrito", "resolucion", "norma", "otro"),
      language: oneOf("es", "en"),
      jurisdiction_id: digits,
      matter_id: uuid,
    },
  }),
  r("GET", `files/${UUID_RE}`),
  r("GET", `files/${UUID_RE}/text`),
  r("DELETE", `files/${UUID_RE}`),
  // ADR-024: model profiles and the firm's opt-in for remote models (the API checks the admin role)
  r("GET", "models"),
  r("POST", "models/policy"),
];

/** Only the parameters the route declares reach the API, each validated; anything else is dropped. */
export function forwardedQuery(search: string, route?: Route): string {
  // no route: the conversations list (the original caller); a route without parameters: none
  const allowed = route ? (route.query ?? {}) : ALLOWED.find((x) => x.pattern.test("conversations"))!.query!;
  const src = new URLSearchParams(search);
  const out = new URLSearchParams();
  for (const [name, check] of Object.entries(allowed)) {
    const v = src.get(name);
    if (v === null) continue;
    const ok = check(v);
    if (ok !== null) out.set(name, ok);
  }
  const s = out.toString();
  return s ? `?${s}` : "";
}

/** CSRF guard for state-changing requests to our own route handlers. A cross-site
 *  `<form enctype="text/plain">` can smuggle a valid JSON body (login CSRF: the victim ends up in
 *  the attacker's account). Requiring a JSON (or PDF / octet-stream) content type forces a CORS preflight we never
 *  grant; the Fetch-Metadata / Origin checks reject what a browser marks as cross-site. */
export function isSameOriginJson(headers: Headers, type: BodyKind = "json"): boolean {
  const got = (headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (got !== BODY_TYPE[type]) return false;
  const site = headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return false;
  const origin = headers.get("origin");
  if (!origin) return true;
  // Compare with the Host the browser actually used: Next's `request.url` may say `localhost`
  // when the page was opened as 127.0.0.1 (measured), which would block the legitimate login.
  try {
    return new URL(origin).host === (headers.get("host") ?? "");
  } catch {
    return false;
  }
}

export function match(method: string, segments: string[]): Route | null {
  const path = segments.join("/");
  if (segments.some((s) => s === "" || s === "." || s === ".." || s.includes("\\") || s.includes("%"))) return null;
  return ALLOWED.find((x) => x.method === method && x.pattern.test(path)) ?? null;
}
