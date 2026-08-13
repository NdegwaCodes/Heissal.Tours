// Shared constants for the BFF auth layer.
// Tokens live in httpOnly cookies; the browser never reads them directly.

export const ACCESS_COOKIE = "heissal_access";
export const REFRESH_COOKIE = "heissal_refresh";

export const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
export const API_V1 = "/api/v1";

// Match token lifetimes (see backend settings).
export const ACCESS_MAX_AGE = 60 * 15; // 15 minutes
export const REFRESH_MAX_AGE = 60 * 60 * 24 * 7; // 7 days

export const cookieOptions = (maxAge: number) => ({
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  maxAge,
});
