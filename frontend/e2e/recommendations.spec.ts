import { test, expect } from "@playwright/test";

/**
 * Recommendations page tests
 */
test.describe("Recommendations Page", () => {
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

  test("recommendations page renders with data", async ({ page }) => {
    // Mock recommendations response
    await page.route("**/api/my/recommendations*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          recommendations: [
            {
              song_id: "rec1",
              artist: "ABBA",
              title: "Dancing Queen",
              score: 0.92,
              reason: "You love other songs by this artist",
              reason_type: "known_artist",
              brand_count: 8,
              popularity: 85,
            },
            {
              song_id: "rec2",
              artist: "Bon Jovi",
              title: "Livin' on a Prayer",
              score: 0.87,
              reason: "Popular karaoke classic",
              reason_type: "crowd_pleaser",
              brand_count: 12,
              popularity: 90,
            },
          ],
        }),
      });
    });

    await page.goto("/recommendations");
    await page.waitForLoadState("networkidle");

    // Check heading
    await expect(page.getByText("Recommendations")).toBeVisible({ timeout: 10000 });

    // Check recommendation cards
    await expect(page.getByText("Dancing Queen")).toBeVisible();
    await expect(page.getByText("ABBA")).toBeVisible();
    await expect(page.getByText("Livin' on a Prayer")).toBeVisible();

    // Check reason badges
    await expect(page.getByText(/you love other songs/i)).toBeVisible();
  });

  test("recommendations page has filter controls", async ({ page }) => {
    await page.route("**/api/my/recommendations*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ recommendations: [] }),
      });
    });

    await page.goto("/recommendations");
    await page.waitForLoadState("networkidle");

    // Check decade filter
    const decadeSelect = page.locator('select').first();
    await expect(decadeSelect).toBeVisible({ timeout: 10000 });

    // Check popularity filter
    const popularitySelect = page.locator('select').nth(1);
    await expect(popularitySelect).toBeVisible();
  });

  test("recommendations shows empty state", async ({ page }) => {
    await page.route("**/api/my/recommendations*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ recommendations: [] }),
      });
    });

    await page.goto("/recommendations");
    await page.waitForLoadState("networkidle");

    // Check empty state
    await expect(page.getByText(/no recommendations yet/i)).toBeVisible({ timeout: 10000 });
  });

  test("sing it button opens youtube search", async ({ page, context }) => {
    await page.route("**/api/my/recommendations*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          recommendations: [
            {
              song_id: "rec1",
              artist: "ABBA",
              title: "Dancing Queen",
              score: 0.92,
              reason: "You love other songs by this artist",
              reason_type: "known_artist",
              brand_count: 8,
              popularity: 85,
            },
          ],
        }),
      });
    });

    await page.goto("/recommendations");
    await page.waitForLoadState("networkidle");

    // Wait for card to appear
    await expect(page.getByText("Dancing Queen")).toBeVisible({ timeout: 10000 });

    // Listen for new page/tab
    const pagePromise = context.waitForEvent("page");

    // Click sing button
    await page.getByRole("button", { name: /sing it/i }).first().click();

    // Verify YouTube opens
    const newPage = await pagePromise;
    expect(newPage.url()).toContain("youtube.com");
  });
});
