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

// BFF proxy: forwards /api/proxy/<path> to the FastAPI backend with the
// httpOnly access token attached. On 401 it rotates via the refresh token,
// sets the new cookies, and retries once. This keeps tokens out of browser JS.

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const target = path.join("/");
  const search = req.nextUrl.search;
  const method = req.method;
  const hasBody = method !== "GET" && method !== "HEAD";
  const bodyText = hasBody ? await req.text() : undefined;

  const access = req.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;

  const doFetch = (token?: string) =>
    fetch(`${API_BASE}${API_V1}/${target}${search}`, {
      method,
      headers: {
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: bodyText,
      cache: "no-store",
    });

  let apiRes = await doFetch(access);
  let rotated: { access_token: string; refresh_token: string } | null = null;

  if (apiRes.status === 401 && refresh) {
    const r = await fetch(`${API_BASE}${API_V1}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      cache: "no-store",
    });
    if (r.ok) {
      rotated = await r.json();
      apiRes = await doFetch(rotated!.access_token);
    }
  }

  const respText = await apiRes.text();
  const out = new NextResponse(respText, {
    status: apiRes.status,
    headers: { "Content-Type": apiRes.headers.get("content-type") ?? "application/json" },
  });

  if (rotated) {
    out.cookies.set(ACCESS_COOKIE, rotated.access_token, cookieOptions(ACCESS_MAX_AGE));
    out.cookies.set(REFRESH_COOKIE, rotated.refresh_token, cookieOptions(REFRESH_MAX_AGE));
  } else if (apiRes.status === 401) {
    out.cookies.delete(ACCESS_COOKIE);
    out.cookies.delete(REFRESH_COOKIE);
  }
  return out;
}

export {
  handle as GET,
  handle as POST,
  handle as PATCH,
  handle as PUT,
  handle as DELETE,
};
