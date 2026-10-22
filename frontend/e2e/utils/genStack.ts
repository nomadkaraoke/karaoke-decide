/**
 * Helpers for the gen local stack (sign-in codes are exposed at /api/test/emails).
 */

export async function waitForLoginCode(
  genStackUrl: string,
  email: string,
  timeoutMs = 15000
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await fetch(
      `${genStackUrl}/api/test/emails?to=${encodeURIComponent(email)}`
    );
    if (res.ok) {
      const { emails } = (await res.json()) as {
        emails: Array<{ text?: string; html?: string; subject?: string }>;
      };
      // Newest first is not guaranteed; scan from the end
      for (const mail of [...emails].reverse()) {
        const body = `${mail.subject ?? ""}\n${mail.text ?? mail.html ?? ""}`;
        const match = body.match(/\b(\d{6})\b/);
        if (match) return match[1];
      }
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no login code for ${email} within ${timeoutMs}ms`);
}
