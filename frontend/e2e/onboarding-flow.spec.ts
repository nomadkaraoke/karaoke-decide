import { test, expect } from "@playwright/test";

/**
 * Onboarding entry points for signed-out visitors.
 *
 * Guest sessions no longer exist: the quiz and recommendations need a Nomad
 * Karaoke (gen) account, so "Get started" sends visitors to /login and
 * brings them back to /quiz afterwards. The quiz steps themselves are covered
 * (with mocked auth) in quiz.spec.ts; the sign-in flow in gen-signin.spec.ts
 * and the guest-data claim in claim-guest.spec.ts.
 */
test.describe("Onboarding Flow (signed out)", () => {
  test.beforeEach(async ({ context, page }) => {
    await context.clearCookies();
    await page.goto("/en/");
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });
  });

  test("Get started while signed out lands on login, then returns to the quiz", async ({
    page,
  }) => {
    await page.goto("/en/");
    await page.waitForLoadState("networkidle");

    await page.locator("button").filter({ hasText: /get started/i }).click();

    await page.waitForURL(/\/en\/login/, { timeout: 10000 });
    expect(decodeURIComponent(new URL(page.url()).search)).toContain("next=/quiz");
    await expect(page.getByTestId("login-email")).toBeVisible();
  });

  test("visiting the quiz while signed out redirects to login with next=/quiz", async ({
    page,
  }) => {
    await page.goto("/en/quiz");

    await page.waitForURL(/\/en\/login/, { timeout: 10000 });
    expect(decodeURIComponent(new URL(page.url()).search)).toContain("next=/quiz");
  });
});
