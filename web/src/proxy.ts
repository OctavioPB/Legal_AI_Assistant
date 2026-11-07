import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, isExpired } from "@/lib/auth";

// Optimistic redirect only (see the Next.js authentication guide): real authorization is the API.
export function proxy(request: NextRequest) {
  if (isExpired(request.cookies.get(SESSION_COOKIE)?.value)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!login|api/session|_next/static|_next/image|fonts|favicon.ico).*)"],
};
