/**
 * Sign-in against the Nomad Karaoke (gen) API.
 *
 * Requests go straight to gen (CORS allows any origin). The returned
 * `session_token` is stored where the decide JWT used to be and sent by
 * api.ts as the bearer; decide's backend validates it against gen's sessions.
 */

import { GEN_API_URL } from "./constants";

export class GenAuthError extends Error {
  constructor(
    public status: number,
    /** gen's machine-readable `detail`, e.g. "invalid_code", "expired" */
    public code: string
  ) {
    super(code);
    this.name = "GenAuthError";
  }
}

async function post(
  path: string,
  body: unknown,
  token?: string
): Promise<Record<string, unknown>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(`${GEN_API_URL}/api/users${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "error";
    throw new GenAuthError(response.status, detail);
  }
  return data;
}

function sessionTokenOf(data: Record<string, unknown>): string {
  const token = data.session_token;
  if (typeof token !== "string" || !token) {
    throw new GenAuthError(500, "no_session_token");
  }
  return token;
}

export async function sendCode(email: string, locale: string): Promise<void> {
  await post("/auth/send-code", { email: email.trim(), locale });
}

export async function verifyCode(email: string, code: string): Promise<string> {
  return sessionTokenOf(
    await post("/auth/verify-code", { email: email.trim(), code: code.trim() })
  );
}

export async function googleSignIn(idToken: string): Promise<string> {
  return sessionTokenOf(await post("/auth/google", { id_token: idToken }));
}

export async function logout(token: string): Promise<void> {
  await post("/auth/logout", {}, token);
}
