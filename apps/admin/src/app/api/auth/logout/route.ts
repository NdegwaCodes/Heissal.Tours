import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, API_BASE, API_V1, REFRESH_COOKIE } from "@/lib/session";

export async function POST(req: NextRequest) {
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  if (refresh) {
    await fetch(`${API_BASE}${API_V1}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      cache: "no-store",
    }).catch(() => {});
  }
  const out = NextResponse.json({ ok: true });
  out.cookies.delete(ACCESS_COOKIE);
  out.cookies.delete(REFRESH_COOKIE);
  return out;
}
