import { test, expect } from "@playwright/test";

/**
 * Services page redirect tests and Spotify OAuth callback pages
 *
 * The /services page now redirects to /my-data.
 * Full functionality tests are in my-data.spec.ts
 *
 * This file keeps tests for Spotify OAuth callback pages which remain unchanged.
 */
test.describe("Services Redirect", () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication
    await page.goto("/");
    await page.evaluate(() => {
      localStorage.setItem("karaoke_decide_token", "test-token-12345");
    });

    await page.route("**/api/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "user_test123",
          email: "test@example.com",
          display_name: "Test User",
          is_guest: false,
        }),
      });
    });

    // Mock endpoints needed by my-data page
    await page.route("**/api/my/data/summary", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: {},
          artists: { total: 0, by_source: {} },
          songs: { total: 0, with_karaoke: 0 },
          preferences: { completed: false },
        }),
      });
    });

    await page.route("**/api/services/sync/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services: [], active_job: null }),
      });
    });
  });

  test("/services redirects to /my-data", async ({ page }) => {
    await page.goto("/services");
    await page.waitForURL("**/my-data");

    // Should be on my-data page
    await expect(page.getByRole("heading", { name: "My Data" })).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Spotify OAuth Callbacks", () => {
  test("spotify success page shows success message", async ({ page }) => {
    await page.goto("/services/spotify/success");

    await expect(page.getByText(/spotify connected/i)).toBeVisible({ timeout: 5000 });
  });

  test("spotify error page shows error message", async ({ page }) => {
    await page.goto("/services/spotify/error?message=access_denied");

    await expect(page.getByText(/connection failed/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("access_denied")).toBeVisible();
    await expect(page.getByRole("link", { name: /try again/i })).toBeVisible();
  });
});
