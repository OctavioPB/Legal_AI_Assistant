import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE, isExpired } from "@/lib/auth";
import { BODY_TYPE, forwardedQuery, isSameOriginJson, match } from "@/lib/proxy";

// Same-origin gateway to the API: adds the bearer from the httpOnly cookie, forwards only the
// allow-listed routes, caps the body and passes SSE through untouched.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const MAX_BODY = 48 * 1024; // a case carries facts + claim (8000 characters each, multibyte)
const MAX_JSON_BODY = 1024 * 1024; // a brief pasted into the verifier (400 000 characters at most)
const MAX_PDF = 15 * 1024 * 1024; // the verifier's PDF cap (the API enforces the same)
const MAX_FILE = 25 * 1024 * 1024; // ADR-025: an uploaded document (the API enforces the same)
const PASS_HEADERS = ["content-type", "retry-after", "cache-control"];

async function forward(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const route = match(request.method, path);
  if (!route) return NextResponse.json({ detail: "not found" }, { status: 404 });

  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token || isExpired(token)) return NextResponse.json({ detail: "session expired" }, { status: 401 });

  let body: string | ArrayBuffer | undefined;
  const kind = route.body ?? "json";
  if (request.method !== "GET") {
    // Defense in depth: the SameSite=Strict cookie already stays home on cross-site requests.
    if (!isSameOriginJson(request.headers, kind)) {
      return NextResponse.json({ detail: "forbidden" }, { status: 403 });
    }
    if (kind !== "json") {
      body = await request.arrayBuffer();
      if (body.byteLength > (kind === "pdf" ? MAX_PDF : MAX_FILE)) return NextResponse.json({ detail: "body too large" }, { status: 413 });
    } else {
      body = await request.text();
      const cap = path.join("/") === "verify" ? MAX_JSON_BODY : MAX_BODY;
      if (new TextEncoder().encode(body).length > cap) return NextResponse.json({ detail: "body too large" }, { status: 413 });
    }
  }
  let upstream: Response;
  try {
    const query = route.query ? forwardedQuery(new URL(request.url).search, route) : "";
    upstream = await fetch(`${API_URL}/${path.join("/")}${query}`, {
      method: request.method,
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": BODY_TYPE[kind],
        accept: route.stream ? "text/event-stream" : "application/json",
      },
      body: body || undefined,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "service unavailable", code: "api_down" }, { status: 503 });
  }
  const headers = new Headers();
  for (const h of PASS_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) headers.set(h, v);
  }
  if (route.stream) headers.set("cache-control", "no-store, no-transform");
  return new Response(upstream.body, { status: upstream.status, headers });
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const DELETE = forward;
