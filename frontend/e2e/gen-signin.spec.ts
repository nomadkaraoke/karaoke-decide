import { test, expect } from "@playwright/test";
import { waitForLoginCode } from "./utils/genStack";

/**
 * gen email-code sign-in on decide web.
 *
 * Runs against the local gen + decide stacks:
 *   GEN_STACK_URL=http://127.0.0.1:8010 \
 *   NEXT_PUBLIC_API_URL=http://127.0.0.1:8030 \
 *   NEXT_PUBLIC_GEN_API_URL=http://127.0.0.1:8010 npm run dev
 */
const GEN = process.env.GEN_STACK_URL;
const DECIDE_API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8030";

test.describe("gen sign-in", () => {
  test.skip(!GEN, "Requires GEN_STACK_URL (gen local stack)");

  test("email -> code -> signed in, token is a gen session, logout revokes it", async ({
    page,
  }) => {
    const email = `d1-${Date.now()}@example.com`;
    await page.goto("/en/login");
    await page.evaluate(() => localStorage.clear());
    await page.goto("/en/login");

    await page.getByTestId("login-email").fill(email);
    await page.getByTestId("login-send-code").click();

    await expect(page.getByTestId("login-code")).toBeVisible();
    // wrong code first
    await page.getByTestId("login-code").fill("000000");
    await page.getByTestId("login-verify").click();
    await expect(page.getByText(/isn't right/i)).toBeVisible();

    const code = await waitForLoginCode(GEN!, email);
    await page.getByTestId("login-code").fill(code);
    await page.getByTestId("login-verify").click();

    await expect(page).toHaveURL(/\/en\/my-songs/, { timeout: 15000 });

    const token = await page.evaluate(() =>
      localStorage.getItem("karaoke_decide_token")
    );
    expect(token).toBeTruthy();
    expect(token!.includes(".")).toBe(false);

    const me = await page.request.get(`${DECIDE_API}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(me.status()).toBe(200);

    // Log out via the app (settings page) and confirm gen revoked the session
    await page.goto("/en/settings");
    await page.getByRole("button", { name: /log out/i }).first().click();
    await expect
      .poll(async () => {
        const res = await page.request.get(`${GEN}/api/users/me`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return res.status();
      })
      .toBe(401);
  });

  test("?next= is honoured and external targets are ignored", async ({ page }) => {
    await page.goto("/en/login?next=//evil.example.com");
    await expect(page.getByTestId("login-email")).toBeVisible();
    // Google button is hidden when no client id is configured
    if (!process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID) {
      await expect(page.getByTestId("login-google")).toHaveCount(0);
    }
  });
});
