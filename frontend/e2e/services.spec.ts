import { test, expect } from "@playwright/test";

/**
 * Services page tests
 */
test.describe("Music Services Page", () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication
    await page.goto("/");
    await page.evaluate(() => {
      localStorage.setItem("karaoke_decide_token", "test-token-12345");
    });

    // Mock the auth check
    await page.route("**/api/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "user_test123",
          email: "test@example.com",
          display_name: "Test User",
        }),
      });
    });
  });

  test("services page shows Spotify and Last.fm sections", async ({ page }) => {
    await page.route("**/api/services", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    });

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    // Check heading - use first() to handle nav link
    await expect(page.getByRole("heading", { name: "Music Services" })).toBeVisible({ timeout: 10000 });

    // Check Spotify section - find within page content, not nav
    await expect(page.locator("h2").filter({ hasText: "Spotify" })).toBeVisible();
    await expect(page.getByRole("button", { name: /connect spotify/i })).toBeVisible();

    // Check Last.fm section
    await expect(page.locator("h2").filter({ hasText: "Last.fm" })).toBeVisible();
  });

  test("services page shows connected status", async ({ page }) => {
    await page.route("**/api/services", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            service_type: "spotify",
            service_username: "testuser",
            last_sync_at: "2024-12-30T10:00:00Z",
            sync_status: "idle",
            sync_error: null,
            tracks_synced: 150,
          },
        ]),
      });
    });

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    // Check connected badge
    await expect(page.getByText("Connected").first()).toBeVisible({ timeout: 10000 });

    // Check username is displayed
    await expect(page.getByText("testuser")).toBeVisible();

    // Check tracks synced
    await expect(page.getByText(/150 tracks synced/i)).toBeVisible();

    // Check disconnect button
    await expect(page.getByRole("button", { name: /disconnect/i })).toBeVisible();
  });

  test("can enter Last.fm username", async ({ page }) => {
    await page.route("**/api/services", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    });

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    // Find Last.fm username input
    const usernameInput = page.getByPlaceholder(/last.fm username/i);
    await expect(usernameInput).toBeVisible({ timeout: 10000 });

    // Enter username
    await usernameInput.fill("myusername");
    await expect(usernameInput).toHaveValue("myusername");
  });

  test("sync button appears when services are connected", async ({ page }) => {
    await page.route("**/api/services", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            service_type: "spotify",
            service_username: "testuser",
            last_sync_at: null,
            sync_status: "idle",
            sync_error: null,
            tracks_synced: 0,
          },
        ]),
      });
    });

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    // Check sync button
    await expect(page.getByRole("button", { name: /sync now/i })).toBeVisible({ timeout: 10000 });
  });

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
