import { NextRequest, NextResponse } from "next/server";
import {
  ACCESS_COOKIE,
  ACCESS_MAX_AGE,
  API_BASE,
  API_V1,
  REFRESH_COOKIE,
  REFRESH_MAX_AGE,
  cookieOptions,
} from "@/lib/session";

export async function POST(req: NextRequest) {
  const { email, password } = await req.json();
  const form = new URLSearchParams({ username: email ?? "", password: password ?? "" });

  const res = await fetch(`${API_BASE}${API_V1}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
    cache: "no-store",
  });

  if (!res.ok) {
    const data = await res.json().catch(() => null);
    return NextResponse.json(
      data ?? { error: { code: "ERROR", message: "Login failed" } },
      { status: res.status },
    );
  }

  const tokens = await res.json();
  const out = NextResponse.json({ ok: true });
  out.cookies.set(ACCESS_COOKIE, tokens.access_token, cookieOptions(ACCESS_MAX_AGE));
  out.cookies.set(REFRESH_COOKIE, tokens.refresh_token, cookieOptions(REFRESH_MAX_AGE));
  return out;
}
