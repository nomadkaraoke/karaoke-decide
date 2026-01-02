import { test, expect } from "@playwright/test";

/**
 * Quiz onboarding flow tests
 * Tests the 4-step quiz: Genre Selection → Artist Selection → Preferences → Results
 * Uses data-testid selectors for maintainability
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

    // Mock quiz artists
    await page.route("**/api/quiz/artists*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          artists: [
            {
              name: "Queen",
              song_count: 58,
              top_songs: ["Bohemian Rhapsody", "Don't Stop Me Now", "We Will Rock You"],
              total_brand_count: 15,
              primary_decade: "1970s",
              genres: ["classic rock", "glam rock", "arena rock"],
              image_url: null,
            },
            {
              name: "Journey",
              song_count: 35,
              top_songs: ["Don't Stop Believin'", "Open Arms", "Faithfully"],
              total_brand_count: 12,
              primary_decade: "1980s",
              genres: ["classic rock", "arena rock", "soft rock"],
              image_url: null,
            },
            {
              name: "ABBA",
              song_count: 42,
              top_songs: ["Dancing Queen", "Mamma Mia", "Take a Chance on Me"],
              total_brand_count: 10,
              primary_decade: "1970s",
              genres: ["pop", "disco", "europop"],
              image_url: null,
            },
          ],
        }),
      });
    });

    // Mock decade artists
    await page.route("**/api/quiz/decade-artists*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          decades: [
            {
              decade: "1970s",
              artists: [
                { name: "Queen", top_song: "Bohemian Rhapsody" },
                { name: "ABBA", top_song: "Dancing Queen" },
                { name: "Elton John", top_song: "Tiny Dancer" },
              ],
            },
            {
              decade: "1980s",
              artists: [
                { name: "Michael Jackson", top_song: "Billie Jean" },
                { name: "Prince", top_song: "Purple Rain" },
                { name: "Madonna", top_song: "Like a Prayer" },
              ],
            },
            {
              decade: "1990s",
              artists: [
                { name: "Mariah Carey", top_song: "Vision of Love" },
                { name: "Backstreet Boys", top_song: "I Want It That Way" },
                { name: "Nirvana", top_song: "Smells Like Teen Spirit" },
              ],
            },
            {
              decade: "2000s",
              artists: [
                { name: "Beyoncé", top_song: "Crazy in Love" },
                { name: "Eminem", top_song: "Lose Yourself" },
                { name: "Amy Winehouse", top_song: "Valerie" },
              ],
            },
            {
              decade: "2010s",
              artists: [
                { name: "Adele", top_song: "Rolling in the Deep" },
                { name: "Bruno Mars", top_song: "Uptown Funk" },
                { name: "Taylor Swift", top_song: "Shake It Off" },
              ],
            },
            {
              decade: "2020s",
              artists: [
                { name: "Dua Lipa", top_song: "Levitating" },
                { name: "The Weeknd", top_song: "Blinding Lights" },
                { name: "Olivia Rodrigo", top_song: "drivers license" },
              ],
            },
          ],
        }),
      });
    });
  });

  test("quiz page shows genre selection (step 1)", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Check heading for genre selection using data-testid
    await expect(page.getByTestId("quiz-heading")).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("quiz-heading")).toHaveText(/what music do you like/i);

    // Check genre cards are displayed using data-testid
    await expect(page.getByTestId("genre-pop")).toBeVisible();
    await expect(page.getByTestId("genre-rock")).toBeVisible();
    await expect(page.getByTestId("genre-country")).toBeVisible();

    // Check progress indicator (4 dots) using data-testid
    await expect(page.getByTestId("progress-indicator")).toBeVisible();
    await expect(page.getByTestId("progress-dot-1")).toBeVisible();
    await expect(page.getByTestId("progress-dot-2")).toBeVisible();
    await expect(page.getByTestId("progress-dot-3")).toBeVisible();
    await expect(page.getByTestId("progress-dot-4")).toBeVisible();
  });

  test("can select genres in quiz", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Wait for genres to appear using data-testid
    await expect(page.getByTestId("genre-pop")).toBeVisible({ timeout: 10000 });

    // Initially shows instruction using data-testid
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/select genres or skip/i);

    // Click to select Pop
    await page.getByTestId("genre-pop").click();

    // Now shows "1 genre selected"
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/1 genre selected/i);

    // Select another genre
    await page.getByTestId("genre-rock").click();

    // Now shows "2 genres selected"
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/2 genres selected/i);
  });

  test("can navigate to step 2 (artist selection)", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Wait for step 1 content using data-testid
    await expect(page.getByTestId("quiz-heading")).toBeVisible({ timeout: 10000 });

    // Click continue/skip
    await page.getByRole("button", { name: /skip|continue/i }).click();

    // Should see step 2 content (artist selection) using data-testid
    await expect(page.getByTestId("artist-heading")).toBeVisible();
    await expect(page.getByTestId("artist-heading")).toHaveText(/which artists do you know/i);
    await expect(page.getByTestId("artist-grid")).toBeVisible();
  });

  test("step 2 has artist cards and load more button", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /skip|continue/i }).click();

    // Check artist heading and grid using data-testid
    await expect(page.getByTestId("artist-heading")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("artist-grid")).toBeVisible();

    // Check "Show More Artists" button exists using data-testid
    await expect(page.getByTestId("load-more-artists-btn")).toBeVisible();

    // Check "I don't know any" option using data-testid
    await expect(page.getByTestId("skip-artists-btn")).toBeVisible();
  });

  test("step 3 has decade and energy options", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /skip|continue/i }).click();
    await expect(page.getByTestId("artist-heading")).toBeVisible({ timeout: 5000 });

    // Navigate to step 3
    await page.getByRole("button", { name: /continue/i }).click();

    // Check preferences heading using data-testid
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("preferences-heading")).toHaveText(/your preferences/i);

    // Check decade options using data-testid
    await expect(page.getByTestId("decade-section")).toBeVisible();
    await expect(page.getByTestId("decade-1980s")).toBeVisible();
    await expect(page.getByTestId("decade-1990s")).toBeVisible();

    // Check energy options using data-testid
    await expect(page.getByTestId("energy-section")).toBeVisible();
    await expect(page.getByTestId("energy-chill")).toBeVisible();
    await expect(page.getByTestId("energy-medium")).toBeVisible();
    await expect(page.getByTestId("energy-high")).toBeVisible();
  });

  test("can submit quiz and see results with connect CTA", async ({ page }) => {
    await page.route("**/api/quiz/submit", async (route) => {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Quiz completed successfully",
          songs_added: 15,
          recommendations_ready: true,
        }),
      });
    });

    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate through all steps
    // Step 1 → Step 2
    await page.getByRole("button", { name: /skip|continue/i }).click();
    await expect(page.getByTestId("artist-heading")).toBeVisible({ timeout: 5000 });

    // Step 2 → Step 3
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });

    // Click finish
    await page.getByRole("button", { name: /finish quiz/i }).click();

    // Should see success state (step 4) using data-testid
    await expect(page.getByTestId("results-section")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("results-heading")).toHaveText(/quiz complete/i);
    await expect(page.getByTestId("results-message")).toContainText(/found 15 karaoke songs/i);
    await expect(page.getByTestId("view-recommendations-btn")).toBeVisible();

    // Should see connect CTA using data-testid
    await expect(page.getByTestId("connect-cta")).toBeVisible();
    await expect(page.getByTestId("connect-cta-heading")).toHaveText(/want even better recommendations/i);
    await expect(page.getByTestId("connect-spotify-btn")).toBeVisible();
  });
});
