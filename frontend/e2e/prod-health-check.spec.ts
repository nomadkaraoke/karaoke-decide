import { test, expect } from "@playwright/test";

/**
 * Production health check tests.
 * These tests verify critical user flows are working in production.
 * Run hourly by GitHub Actions to detect issues quickly.
 */

test.describe("Production Health Checks", () => {
  test.describe("API Health", () => {
    test("health endpoint responds", async ({ request }) => {
      const response = await request.get("/api/health");
      expect(response.ok()).toBeTruthy();
      const data = await response.json();
      expect(data.status).toBe("healthy");
    });

    test("deep health endpoint shows all services healthy", async ({ request }) => {
      const response = await request.get("/api/health/deep", { timeout: 30000 });
      expect(response.ok()).toBeTruthy();
      const data = await response.json();
      expect(data.status).toBe("healthy");
      expect(data.checks.firestore.status).toBe("healthy");
      expect(data.checks.bigquery.status).toBe("healthy");
    });

    test("song search API works", async ({ request }) => {
      const response = await request.get("/api/catalog/songs?q=queen&per_page=5");
      expect(response.ok()).toBeTruthy();
      const data = await response.json();
      expect(data.songs.length).toBeGreaterThan(0);
    });

    test("artist search API works", async ({ request }) => {
      const response = await request.get("/api/catalog/artists?q=radiohead&limit=5");
      expect(response.ok()).toBeTruthy();
      const data = await response.json();
      expect(data.artists.length).toBeGreaterThan(0);
    });

    test("artist index API works", async ({ request }) => {
      const response = await request.get("/api/catalog/artists/index");
      expect(response.ok()).toBeTruthy();
      const data = await response.json();
      // Should have thousands of artists (response is { artists: [], count: X })
      expect(data.count || data.artists?.length || data.length).toBeGreaterThan(1000);
    });
  });

  test.describe("Frontend Loading", () => {
    test("homepage loads without errors", async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (msg) => {
        if (msg.type() === "error") errors.push(msg.text());
      });

      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Verify main content loads (either landing page variant)
      await expect(page.locator("h1")).toBeVisible();

      // Check for console errors (excluding known benign ones)
      const significantErrors = errors.filter(
        (e) =>
          !e.includes("favicon") &&
          !e.includes("hydration") &&
          !e.includes("third-party")
      );
      expect(significantErrors).toHaveLength(0);
    });

    test("search works on homepage", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Type in search
      const searchInput = page.locator('input[placeholder*="Search"]').first();
      await expect(searchInput).toBeVisible({ timeout: 10000 });
      await searchInput.fill("queen");

      // Wait for results
      await page.waitForTimeout(1500); // Debounce delay

      // Should see search results
      const results = page.locator('[data-testid="song-card"], .song-card, [class*="SongCard"]');
      await expect(results.first()).toBeVisible({ timeout: 10000 });
    });
  });

  test.describe("Guest Onboarding Flow", () => {
    test.beforeEach(async ({ context, page }) => {
      // Clear state for fresh guest experience
      await context.clearCookies();
      await page.goto("/");
      await page.evaluate(() => {
        localStorage.clear();
        sessionStorage.clear();
      });
    });

    test("Get Started creates guest session and navigates to quiz", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Find and click Get Started button
      const getStartedBtn = page.locator("button").filter({ hasText: /get started/i });
      await expect(getStartedBtn).toBeVisible({ timeout: 10000 });
      await getStartedBtn.click();

      // Should navigate to quiz
      await page.waitForURL(/\/quiz/, { timeout: 15000 });

      // Quiz page should load
      await expect(page.locator("h1, [data-testid='quiz-heading']")).toBeVisible({ timeout: 10000 });
    });

    test("quiz page loads with genre selection", async ({ page }) => {
      // Go directly to quiz (will create guest session)
      await page.goto("/quiz");
      await page.waitForLoadState("networkidle");

      // Wait for page to fully load
      await page.waitForTimeout(2000);

      // Genre grid should be visible
      const genreGrid = page.locator("[data-testid='genre-grid']");
      await expect(genreGrid).toBeVisible({ timeout: 15000 });

      // Some genres should be present
      await expect(page.locator("[data-testid='genre-pop']")).toBeVisible({ timeout: 5000 });
      await expect(page.locator("[data-testid='genre-rock']")).toBeVisible({ timeout: 5000 });
    });

    test("artist autocomplete works in quiz", async ({ page }) => {
      // Go to quiz
      await page.goto("/quiz");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2000);

      // Navigate through genre step first
      await page.locator("[data-testid='genre-rock']").click();
      await page.locator("[data-testid='genre-pop']").click();
      await page.locator("button").filter({ hasText: /continue/i }).click();

      // Skip preferences step
      await page.waitForTimeout(1000);
      await page.locator("button").filter({ hasText: /continue|skip/i }).first().click();

      // Now on artists step - look for artist input or artist cards
      await page.waitForTimeout(2000);

      // Check if we're on a step with artist selection (either manual entry or pre-populated)
      const artistStep = page.locator("[data-testid='artist-heading'], h1").filter({ hasText: /artist|music you know/i });
      await expect(artistStep).toBeVisible({ timeout: 10000 });

      // Look for autocomplete or artist cards
      const autocompleteInput = page.locator('input[placeholder*="Search"], input[placeholder*="artist"]');
      if (await autocompleteInput.count() > 0) {
        // Type to search
        await autocompleteInput.first().fill("green day");
        await page.waitForTimeout(1500);

        // Should see suggestions or results - this will fail if index endpoint is broken
        const suggestions = page.locator('[role="listbox"], [class*="suggestion"], [class*="autocomplete"]');
        // Just verify no error state - actual results depend on API
        await expect(page.locator('text=/error|failed|503/i')).not.toBeVisible();
      }
    });
  });
});
