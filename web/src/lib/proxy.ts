// The browser never talks to the API directly and never sees the token: a same-origin route handler
// adds the bearer from the httpOnly cookie. Only these exact routes are forwarded (an open proxy
// would let the page reach any internal path).

export interface Route {
  method: "GET" | "POST";
  pattern: RegExp;
  stream?: boolean;
}

export const ALLOWED: Route[] = [
  { method: "GET", pattern: /^me$/ },
  { method: "GET", pattern: /^catalog\/(jurisdictions|personas)$/ },
  { method: "POST", pattern: /^chat$/ },
  { method: "POST", pattern: /^chat\/stream$/, stream: true },
  { method: "GET", pattern: /^sources\/[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/ },
];

export function match(method: string, segments: string[]): Route | null {
  const path = segments.join("/");
  if (segments.some((s) => s === "" || s === "." || s === ".." || s.includes("\\") || s.includes("%"))) return null;
  return ALLOWED.find((r) => r.method === method && r.pattern.test(path)) ?? null;
}
