// Optimistic, UX-only session check. The API verifies the JWT signature and anchors the tenant
// (RLS); the browser never decides authorization. Claims read here only drive redirects.

export const SESSION_COOKIE = "session";

export interface SessionClaims {
  sub?: string;
  tenant_id?: string;
  exp?: number;
}

function base64UrlDecode(part: string): string {
  const padded = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

export function decodeClaims(token: string | undefined): SessionClaims | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const claims: unknown = JSON.parse(base64UrlDecode(parts[1]));
    return typeof claims === "object" && claims !== null ? (claims as SessionClaims) : null;
  } catch {
    return null;
  }
}

/** True when the token is missing, malformed, has no exp, or is expired. */
export function isExpired(token: string | undefined, nowSeconds = Math.floor(Date.now() / 1000)): boolean {
  const claims = decodeClaims(token);
  if (!claims || typeof claims.exp !== "number") return true;
  return claims.exp <= nowSeconds;
}
