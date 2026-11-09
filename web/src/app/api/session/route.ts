import { NextResponse } from "next/server";
import { SESSION_COOKIE, decodeClaims, isExpired } from "@/lib/auth";
import { isSameOriginJson } from "@/lib/proxy";

// Exchanges credentials for the JWT issued by the API and stores it httpOnly. The browser never
// sees the token. Every failure the user can cause answers the same way (no user enumeration).
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

const MAX_BODY = 4 * 1024;

export async function POST(request: Request) {
  if (!isSameOriginJson(request.headers)) {
    return NextResponse.json({ error: "invalid" }, { status: 403 });
  }
  const raw = await request.text();
  if (raw.length > MAX_BODY) return NextResponse.json({ error: "invalid" }, { status: 413 });
  let body: unknown = null;
  try {
    body = JSON.parse(raw);
  } catch {
    body = null;
  }
  if (typeof body !== "object" || body === null) {
    return NextResponse.json({ error: "invalid" }, { status: 400 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ error: "unavailable" }, { status: 503 });
  }
  if (upstream.status === 401 || upstream.status === 403 || upstream.status === 422) {
    return NextResponse.json({ error: "invalid" }, { status: 401 });
  }
  if (upstream.status === 429) return NextResponse.json({ error: "unavailable" }, { status: 429 });
  if (!upstream.ok) return NextResponse.json({ error: "unavailable" }, { status: 503 });
  const data: { access_token?: string } = await upstream.json().catch(() => ({}));
  const token = data.access_token;
  const claims = decodeClaims(token);
  if (!token || !claims || isExpired(token)) {
    return NextResponse.json({ error: "unavailable" }, { status: 503 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production" && process.env.INSECURE_LOCAL !== "1",
    sameSite: "strict",
    path: "/",
    maxAge: Math.max(1, (claims.exp ?? 0) - Math.floor(Date.now() / 1000)),
  });
  return res;
}

export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
