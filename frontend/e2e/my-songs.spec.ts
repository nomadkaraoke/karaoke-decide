import { test, expect } from "@playwright/test";

/**
 * My Songs page tests
 *
 * Note: These tests mock the API responses since they require authentication.
 * In a real scenario, you would either:
 * 1. Use a test user with seeded data
 * 2. Mock the API at the network level
 */
test.describe("My Songs Page", () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication by setting a token
    await page.goto("/");
    await page.evaluate(() => {
      localStorage.setItem("karaoke_decide_token", "test-token-12345");
    });

    // Mock the auth check - must be in beforeEach for protected pages
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

  test("my-songs page structure is correct", async ({ page }) => {
    // Mock the songs endpoint
    await page.route("**/api/my/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [
            {
              id: "song1",
              song_id: "s1",
              artist: "Queen",
              title: "Bohemian Rhapsody",
              source: "spotify",
              play_count: 50,
              is_saved: true,
              times_sung: 3,
            },
            {
              id: "song2",
              song_id: "s2",
              artist: "Journey",
              title: "Don't Stop Believin'",
              source: "lastfm",
              play_count: 30,
              is_saved: false,
              times_sung: 1,
            },
          ],
          total: 2,
          page: 1,
          per_page: 20,
          has_more: false,
        }),
      });
    });

    await page.goto("/my-songs");

    // Wait for content to load
    await page.waitForLoadState("networkidle");

    // Check heading - use role selector to avoid matching nav link
    await expect(page.getByRole("heading", { name: "My Songs" })).toBeVisible({ timeout: 10000 });

    // Check song count
    await expect(page.getByText("2 songs")).toBeVisible();

    // Check first song
    await expect(page.getByText("Bohemian Rhapsody")).toBeVisible();
    await expect(page.getByText("Queen")).toBeVisible();

    // Check source badge
    await expect(page.getByText("Spotify")).toBeVisible();
  });

  test("my-songs shows empty state when no songs", async ({ page }) => {
    // Mock empty songs response
    await page.route("**/api/my/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [],
          total: 0,
          page: 1,
          per_page: 20,
          has_more: false,
        }),
      });
    });

    await page.goto("/my-songs");
    await page.waitForLoadState("networkidle");

    // Check empty state message
    await expect(page.getByText("No songs yet")).toBeVisible({ timeout: 10000 });
    // Use more specific selector to match only the description text, not the button
    await expect(page.getByText("Connect your music services")).toBeVisible();
  });

  test("my-songs has link to recommendations", async ({ page }) => {
    // Mock songs response
    await page.route("**/api/my/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [],
          total: 0,
          page: 1,
          per_page: 20,
          has_more: false,
        }),
      });
    });

    await page.goto("/my-songs");
    await page.waitForLoadState("networkidle");

    // Check for recommendations link
    await expect(
      page.getByRole("link", { name: /get recommendations/i })
    ).toBeVisible({ timeout: 10000 });
  });
});
