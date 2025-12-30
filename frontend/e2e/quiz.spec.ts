import { test, expect } from "@playwright/test";

/**
 * Quiz onboarding flow tests
 */
test.describe("Quiz Onboarding", () => {
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

  test("quiz page shows song selection (step 1)", async ({ page }) => {
    // Mock quiz songs
    await page.route("**/api/quiz/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [
            {
              id: "quiz1",
              artist: "Queen",
              title: "Bohemian Rhapsody",
              decade: "1970s",
              popularity: 95,
              brand_count: 15,
            },
            {
              id: "quiz2",
              artist: "Journey",
              title: "Don't Stop Believin'",
              decade: "1980s",
              popularity: 90,
              brand_count: 12,
            },
            {
              id: "quiz3",
              artist: "ABBA",
              title: "Dancing Queen",
              decade: "1970s",
              popularity: 88,
              brand_count: 10,
            },
          ],
        }),
      });
    });

    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Check heading
    await expect(page.getByText(/which songs do you know/i)).toBeVisible({ timeout: 10000 });

    // Check song cards are displayed
    await expect(page.getByText("Bohemian Rhapsody")).toBeVisible();
    await expect(page.getByText("Don't Stop Believin'")).toBeVisible();
    await expect(page.getByText("Dancing Queen")).toBeVisible();

    // Check progress indicator (3 dots)
    const dots = page.locator('.rounded-full.w-3.h-3');
    await expect(dots).toHaveCount(3);
  });

  test("can select songs in quiz", async ({ page }) => {
    await page.route("**/api/quiz/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [
            {
              id: "quiz1",
              artist: "Queen",
              title: "Bohemian Rhapsody",
              decade: "1970s",
              popularity: 95,
              brand_count: 15,
            },
          ],
        }),
      });
    });

    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Wait for song to appear
    await expect(page.getByText("Bohemian Rhapsody")).toBeVisible({ timeout: 10000 });

    // Initially shows "0 of 1 selected"
    await expect(page.getByText(/0 of 1 selected/i)).toBeVisible();

    // Click to select
    await page.getByText("Bohemian Rhapsody").click();

    // Now shows "1 of 1 selected"
    await expect(page.getByText(/1 of 1 selected/i)).toBeVisible();
  });

  test("can navigate to step 2 (preferences)", async ({ page }) => {
    await page.route("**/api/quiz/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [
            {
              id: "quiz1",
              artist: "Queen",
              title: "Bohemian Rhapsody",
              decade: "1970s",
              popularity: 95,
              brand_count: 15,
            },
          ],
        }),
      });
    });

    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Wait for step 1 content
    await expect(page.getByText("Bohemian Rhapsody")).toBeVisible({ timeout: 10000 });

    // Click continue
    await page.getByRole("button", { name: /continue/i }).click();

    // Should see step 2 content
    await expect(page.getByText(/your preferences/i)).toBeVisible();
    await expect(page.getByText(/favorite decade/i)).toBeVisible();
    await expect(page.getByText(/energy level/i)).toBeVisible();
  });

  test("step 2 has decade and energy options", async ({ page }) => {
    await page.route("**/api/quiz/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ songs: [] }),
      });
    });

    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /continue/i }).click();

    // Check decade options
    await expect(page.getByRole("button", { name: "1980s" })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("button", { name: "1990s" })).toBeVisible();

    // Check energy options
    await expect(page.getByText("Chill")).toBeVisible();
    await expect(page.getByText("Medium")).toBeVisible();
    await expect(page.getByText("High Energy")).toBeVisible();
  });

  test("can submit quiz and see results", async ({ page }) => {
    await page.route("**/api/quiz/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ songs: [] }),
      });
    });

    await page.route("**/api/quiz/submit", async (route) => {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Quiz completed successfully",
          songs_added: 5,
          recommendations_ready: true,
        }),
      });
    });

    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByText(/your preferences/i)).toBeVisible({ timeout: 5000 });

    // Click finish
    await page.getByRole("button", { name: /finish quiz/i }).click();

    // Should see success state (step 3)
    await expect(page.getByText(/quiz complete/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/added 5 songs/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /view recommendations/i })).toBeVisible();
  });
});
