import { test, expect } from "@playwright/test";

/**
 * My Data page tests
 *
 * Tests for the unified data management page that replaced My Songs and Services.
 */
test.describe("My Data Page", () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication by setting a token
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
          is_guest: false,
        }),
      });
    });
  });

  test("my-data page loads with all sections", async ({ page }) => {
    // Mock summary endpoint
    await page.route("**/api/my/data/summary", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: {
            spotify: { connected: true, username: "testuser", tracks_synced: 100 },
            lastfm: { connected: false },
          },
          artists: { total: 25, by_source: { spotify: 20, quiz: 5 } },
          songs: { total: 50, with_karaoke: 35 },
          preferences: { completed: true, decade: "1980s", energy: "high", genres: ["rock", "pop"] },
        }),
      });
    });

    // Mock services sync status
    await page.route("**/api/services/sync/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: [
            {
              service_type: "spotify",
              service_username: "testuser",
              last_sync_at: new Date().toISOString(),
              sync_status: "completed",
              sync_error: null,
              tracks_synced: 100,
            },
          ],
          active_job: null,
        }),
      });
    });

    await page.goto("/my-data");
    await page.waitForLoadState("networkidle");

    // Check page heading
    await expect(page.getByRole("heading", { name: "My Data" })).toBeVisible({ timeout: 10000 });

    // Check summary stats are displayed
    await expect(page.getByText("1")).toBeVisible(); // 1 service connected
    await expect(page.getByText("25")).toBeVisible(); // 25 artists
    await expect(page.getByText("50")).toBeVisible(); // 50 songs

    // Check section headers exist
    await expect(page.getByText("Connected Services")).toBeVisible();
    await expect(page.getByText("Your Artists")).toBeVisible();
    await expect(page.getByText("Your Songs")).toBeVisible();
    await expect(page.getByText("Preferences")).toBeVisible();
  });

  test("connected services section shows service status", async ({ page }) => {
    // Mock summary endpoint
    await page.route("**/api/my/data/summary", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: { spotify: { connected: true, username: "testuser", tracks_synced: 100 } },
          artists: { total: 0, by_source: {} },
          songs: { total: 0, with_karaoke: 0 },
          preferences: { completed: false },
        }),
      });
    });

    // Mock services sync status
    await page.route("**/api/services/sync/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: [
            {
              service_type: "spotify",
              service_username: "testuser",
              last_sync_at: new Date().toISOString(),
              sync_status: "completed",
              sync_error: null,
              tracks_synced: 100,
            },
          ],
          active_job: null,
        }),
      });
    });

    await page.goto("/my-data");
    await page.waitForLoadState("networkidle");

    // Services section should be expanded by default
    await expect(page.getByText("Spotify")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("testuser")).toBeVisible();
    await expect(page.getByText(/100 tracks/)).toBeVisible();
  });

  test("artists section shows artists grouped by source", async ({ page }) => {
    // Mock summary endpoint
    await page.route("**/api/my/data/summary", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: {},
          artists: { total: 2, by_source: { spotify: 2 } },
          songs: { total: 0, with_karaoke: 0 },
          preferences: { completed: false },
        }),
      });
    });

    // Mock services sync status
    await page.route("**/api/services/sync/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services: [], active_job: null }),
      });
    });

    // Mock artists endpoint
    await page.route("**/api/my/data/artists", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          artists: [
            { artist_name: "Queen", source: "spotify", rank: 1, time_range: "medium_term", popularity: 90, genres: ["rock"], playcount: null },
            { artist_name: "Journey", source: "spotify", rank: 2, time_range: "medium_term", popularity: 85, genres: ["rock"], playcount: null },
          ],
          total: 2,
        }),
      });
    });

    await page.goto("/my-data");
    await page.waitForLoadState("networkidle");

    // Expand artists section
    await page.getByText("Your Artists").click();
    await page.waitForTimeout(500);

    // Check artists are visible
    await expect(page.getByText("Queen")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Journey")).toBeVisible();
  });

  test("preferences section shows user preferences", async ({ page }) => {
    // Mock summary endpoint
    await page.route("**/api/my/data/summary", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: {},
          artists: { total: 0, by_source: {} },
          songs: { total: 0, with_karaoke: 0 },
          preferences: { completed: true, decade: "1980s", energy: "high", genres: ["rock"] },
        }),
      });
    });

    // Mock services sync status
    await page.route("**/api/services/sync/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services: [], active_job: null }),
      });
    });

    // Mock preferences endpoint
    await page.route("**/api/my/data/preferences", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          decade_preference: "1980s",
          energy_preference: "high",
          genres: ["rock", "pop"],
        }),
      });
    });

    await page.goto("/my-data");
    await page.waitForLoadState("networkidle");

    // Expand preferences section
    await page.getByText("Preferences").click();
    await page.waitForTimeout(500);

    // Check preferences are displayed
    await expect(page.getByText("1980s")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("High Energy")).toBeVisible();
    await expect(page.getByText("Rock")).toBeVisible();
  });

  test("guest user sees upgrade prompt for services", async ({ page }) => {
    // Override auth mock for guest
    await page.route("**/api/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "guest_123",
          email: null,
          display_name: null,
          is_guest: true,
        }),
      });
    });

    // Mock summary endpoint
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

    // Mock services sync status
    await page.route("**/api/services/sync/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services: [], active_job: null }),
      });
    });

    await page.goto("/my-data");
    await page.waitForLoadState("networkidle");

    // Guest should see upgrade prompt in services section
    await expect(page.getByText(/create.*account/i)).toBeVisible({ timeout: 10000 });
  });
});

test.describe("My Data Redirects", () => {
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

    // Mock all required endpoints for my-data page
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

  test("/my-songs redirects to /my-data", async ({ page }) => {
    await page.goto("/my-songs");
    await page.waitForURL("**/my-data");

    // Should be on my-data page
    await expect(page.getByRole("heading", { name: "My Data" })).toBeVisible({ timeout: 10000 });
  });

  test("/services redirects to /my-data", async ({ page }) => {
    await page.goto("/services");
    await page.waitForURL("**/my-data");

    // Should be on my-data page
    await expect(page.getByRole("heading", { name: "My Data" })).toBeVisible({ timeout: 10000 });
  });
});
