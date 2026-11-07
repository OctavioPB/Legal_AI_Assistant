import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE, isExpired } from "@/lib/auth";
import { match } from "@/lib/proxy";

// Same-origin gateway to the API: adds the bearer from the httpOnly cookie, forwards only the
// allow-listed routes, caps the body and passes SSE through untouched.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const MAX_BODY = 16 * 1024;
const PASS_HEADERS = ["content-type", "retry-after", "cache-control"];

async function forward(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const route = match(request.method, path);
  if (!route) return NextResponse.json({ detail: "not found" }, { status: 404 });

  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token || isExpired(token)) return NextResponse.json({ detail: "session expired" }, { status: 401 });

  let body: string | undefined;
  if (request.method === "POST") {
    body = await request.text();
    if (body.length > MAX_BODY) return NextResponse.json({ detail: "body too large" }, { status: 413 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/${path.join("/")}`, {
      method: request.method,
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json", accept: route.stream ? "text/event-stream" : "application/json" },
      body,
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
