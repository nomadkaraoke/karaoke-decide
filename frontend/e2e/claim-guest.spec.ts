import { test, expect, Page } from "@playwright/test";

/**
 * A legacy guest session (dotted JWT) is parked when the user signs in with a
 * gen account, and its data is claimed into that account.
 * Fully mocked: no backend required.
 */
const GUEST_JWT = "guest.jwt.token";
const GEN_TOKEN = "gen-session-token-without-dots";
const GUEST_KEY = "karaoke_decide_guest_token";
const TOKEN_KEY = "karaoke_decide_token";

async function mockStack(page: Page, claimStatus: number) {
  const claims: Array<{ auth: string | undefined; body: unknown }> = [];

  await page.route("**/api/auth/me", async (route) => {
    const auth = route.request().headers()["authorization"];
    const isGuest = auth === `Bearer ${GUEST_JWT}`;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: isGuest ? "guest_1" : "user_1",
        email: isGuest ? null : "claimer@example.com",
        display_name: null,
        is_guest: isGuest,
        is_admin: false,
      }),
    });
  });
  await page.route("**/api/quiz/status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ completed: true }),
    })
  );
  await page.route("**/api/users/auth/send-code", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
  );
  await page.route("**/api/users/auth/verify-code", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session_token: GEN_TOKEN }),
    })
  );
  await page.route("**/api/auth/claim-guest", async (route) => {
    claims.push({
      auth: route.request().headers()["authorization"],
      body: route.request().postDataJSON(),
    });
    await route.fulfill({
      status: claimStatus,
      contentType: "application/json",
      body: JSON.stringify(claimStatus === 200 ? { merged: true } : { detail: "x" }),
    });
  });
  return claims;
}

async function signIn(page: Page) {
  await page.goto("/en/login");
  await page.getByTestId("login-email").fill("claimer@example.com");
  await page.getByTestId("login-send-code").click();
  await page.getByTestId("login-code").fill("123456");
  await page.getByTestId("login-verify").click();
}

test.describe("claim guest profile on sign-in", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(
      ([key, token]) => {
        if (!sessionStorage.getItem("seeded")) {
          sessionStorage.setItem("seeded", "1");
          localStorage.clear();
          localStorage.setItem(key, token);
        }
      },
      [TOKEN_KEY, GUEST_JWT]
    );
  });

  test("guest token is claimed with the gen token, toast shown, key removed", async ({
    page,
  }) => {
    const claims = await mockStack(page, 200);
    await signIn(page);

    await expect(page.getByTestId("claim-guest-toast")).toContainText(
      "We moved your quiz answers and songs to your account."
    );
    expect(claims).toHaveLength(1);
    expect(claims[0].body).toEqual({ guest_token: GUEST_JWT });
    expect(claims[0].auth).toBe(`Bearer ${GEN_TOKEN}`);

    const stored = await page.evaluate(
      ([t, g]) => ({ token: localStorage.getItem(t), guest: localStorage.getItem(g) }),
      [TOKEN_KEY, GUEST_KEY]
    );
    expect(stored.token).toBe(GEN_TOKEN);
    expect(stored.guest).toBeNull();
  });

  test("a 404 drops the guest token without a toast", async ({ page }) => {
    const claims = await mockStack(page, 404);
    await signIn(page);

    await expect.poll(() => claims.length).toBe(1);
    await expect
      .poll(() => page.evaluate((g) => localStorage.getItem(g), GUEST_KEY))
      .toBeNull();
    await expect(page.getByTestId("claim-guest-toast")).toHaveCount(0);
  });

  test("a 5xx keeps the guest token so the next check retries", async ({ page }) => {
    const claims = await mockStack(page, 503);
    await signIn(page);

    await expect.poll(() => claims.length).toBe(1);
    await expect(page.getByTestId("claim-guest-toast")).toHaveCount(0);
    expect(await page.evaluate((g) => localStorage.getItem(g), GUEST_KEY)).toBe(
      GUEST_JWT
    );
  });
});
