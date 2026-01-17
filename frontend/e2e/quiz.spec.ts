import { test, expect } from "@playwright/test";

/**
 * Quiz onboarding flow tests
 * Tests the 5-step quiz:
 * 1. How It Works (intro)
 * 2. What Kind of Music? (genres + decades)
 * 3. Artists You Know (manual entry)
 * 4. Karaoke Preferences (prefs + songs)
 * 5. Know Any of These? (smart suggestions)
 *
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

    // Mock smart artists endpoint
    await page.route("**/api/quiz/smart-artists*", async (route) => {
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
          has_more: true,
        }),
      });
    });

    // Mock quiz artists (fallback)
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
          ],
        }),
      });
    });

    // Mock quiz submission
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

    // Mock quiz status
    await page.route("**/api/quiz/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          completed: true,
          completed_at: new Date().toISOString(),
          songs_known_count: 15,
        }),
      });
    });

    // Mock email collection endpoint
    await page.route("**/api/auth/collect-email", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Email collected",
          user_id: "user_test123",
        }),
      });
    });

    // Mock quiz progress endpoint (auto-save)
    await page.route("**/api/quiz/progress", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ saved: true }),
      });
    });
  });

  test("quiz page shows intro (step 1)", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Check heading for intro using data-testid
    await expect(page.getByTestId("quiz-heading")).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("quiz-heading")).toHaveText(/let's find your perfect karaoke songs/i);

    // Check progress indicator (5 dots) using data-testid
    await expect(page.getByTestId("progress-indicator")).toBeVisible();
    await expect(page.getByTestId("progress-dot-1")).toBeVisible();
    await expect(page.getByTestId("progress-dot-2")).toBeVisible();
    await expect(page.getByTestId("progress-dot-3")).toBeVisible();
    await expect(page.getByTestId("progress-dot-4")).toBeVisible();
    await expect(page.getByTestId("progress-dot-5")).toBeVisible();

    // Check "Get Started" button exists
    await expect(page.getByRole("button", { name: /get started/i })).toBeVisible();
  });

  test("can navigate from intro to genre selection (step 2)", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Wait for step 1 content
    await expect(page.getByTestId("quiz-heading")).toBeVisible({ timeout: 10000 });

    // Click Get Started
    await page.getByRole("button", { name: /get started/i }).click();

    // Should see step 2 content (genres + decades)
    await expect(page.getByTestId("music-taste-heading")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("music-taste-heading")).toHaveText(/what kind of music do you listen to/i);

    // Check genre grid is displayed
    await expect(page.getByTestId("genre-grid")).toBeVisible();
    await expect(page.getByTestId("genre-pop")).toBeVisible();
    await expect(page.getByTestId("genre-rock")).toBeVisible();

    // Check decades section is also visible
    await expect(page.getByTestId("decades-heading")).toBeVisible();
    await expect(page.getByTestId("decade-section")).toBeVisible();
    await expect(page.getByTestId("decade-1980s")).toBeVisible();
  });

  test("can select genres and decades in step 2", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 10000 });

    // Initially shows instruction
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/select at least one genre/i);

    // Continue button should be disabled
    const continueBtn = page.getByRole("button", { name: /continue/i });
    await expect(continueBtn).toBeDisabled();

    // Click to select Pop
    await page.getByTestId("genre-pop").click();

    // Now shows "1 genre selected"
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/1 genre selected/i);

    // Continue button should now be enabled
    await expect(continueBtn).toBeEnabled();

    // Select another genre
    await page.getByTestId("genre-rock").click();

    // Now shows "2 genres selected"
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/2 genres selected/i);

    // Can also select decades
    await page.getByTestId("decade-1980s").click();
    await page.getByTestId("decade-1990s").click();
  });

  test("can navigate to step 3 (artists you know)", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 5000 });

    // Select a genre to enable continue
    await page.getByTestId("genre-pop").click();

    // Navigate to step 3
    await page.getByRole("button", { name: /continue/i }).click();

    // Should see step 3 content (artists you know)
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("artists-you-know-heading")).toHaveText(/artists you know/i);
  });

  test("step 4 has karaoke preferences and songs section", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate through steps 1-3
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 5000 });
    await page.getByTestId("genre-pop").click();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /continue/i }).click();

    // Should see step 4 content (karaoke preferences)
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("preferences-heading")).toHaveText(/karaoke preferences/i);

    // Check energy options
    await expect(page.getByTestId("energy-section")).toBeVisible();
    await expect(page.getByTestId("energy-chill")).toBeVisible();
    await expect(page.getByTestId("energy-medium")).toBeVisible();
    await expect(page.getByTestId("energy-high")).toBeVisible();

    // Check vocal comfort options
    await expect(page.getByTestId("vocal-comfort-section")).toBeVisible();
    await expect(page.getByTestId("vocal-comfort-easy")).toBeVisible();
    await expect(page.getByTestId("vocal-comfort-challenging")).toBeVisible();

    // Check crowd pleaser options
    await expect(page.getByTestId("crowd-pleaser-section")).toBeVisible();
    await expect(page.getByTestId("crowd-pleaser-hits")).toBeVisible();
    await expect(page.getByTestId("crowd-pleaser-deep_cuts")).toBeVisible();
  });

  test("step 5 shows smart artist suggestions", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate through steps 1-4
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 5000 });
    await page.getByTestId("genre-pop").click();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /continue/i }).click();

    // Should see step 5 content (smart artist suggestions)
    await expect(page.getByTestId("smart-artists-heading")).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("smart-artists-heading")).toHaveText(/know any of these artists/i);

    // Check artist grid is displayed
    await expect(page.getByTestId("artist-grid")).toBeVisible();
  });

  test("can complete full quiz flow", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Step 1: Intro
    await expect(page.getByTestId("quiz-heading")).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /get started/i }).click();

    // Step 2: Genres + Decades
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 5000 });
    await page.getByTestId("genre-pop").click();
    await page.getByTestId("genre-rock").click();
    await page.getByTestId("decade-1980s").click();
    await page.getByRole("button", { name: /continue/i }).click();

    // Step 3: Artists You Know
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /continue/i }).click();

    // Step 4: Karaoke Preferences
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });
    await page.getByTestId("energy-medium").click();
    await page.getByTestId("vocal-comfort-easy").click();
    await page.getByRole("button", { name: /continue/i }).click();

    // Step 5: Smart Artist Suggestions - finish quiz
    await expect(page.getByTestId("smart-artists-heading")).toBeVisible({ timeout: 10000 });

    // Click finish quiz
    await page.getByRole("button", { name: /finish quiz/i }).click();

    // Email modal should appear - enter email and continue
    const emailInput = page.getByPlaceholder(/your@email.com/i);
    await expect(emailInput).toBeVisible({ timeout: 10000 });
    await emailInput.fill("test@example.com");
    await page.getByRole("button", { name: /continue/i }).click();

    // Should navigate to recommendations
    await page.waitForURL(/\/recommendations/, { timeout: 10000 });
  });

  test("can skip to recommendations from step 2", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 5000 });

    // Select a genre (required)
    await page.getByTestId("genre-pop").click();

    // Click skip to recommendations
    await page.getByRole("button", { name: /skip to recommendations/i }).click();

    // Email modal should appear - enter email and continue
    const emailInput = page.getByPlaceholder(/your@email.com/i);
    await expect(emailInput).toBeVisible({ timeout: 10000 });
    await emailInput.fill("test@example.com");
    await page.getByRole("button", { name: /continue/i }).click();

    // Should navigate to recommendations
    await page.waitForURL(/\/recommendations/, { timeout: 10000 });
  });

  test("back button navigation works correctly", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Navigate to step 2
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("music-taste-heading")).toBeVisible({ timeout: 5000 });

    // Select genre and go to step 3
    await page.getByTestId("genre-pop").click();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });

    // Go back to step 2
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("music-taste-heading")).toBeVisible({ timeout: 5000 });

    // Genre selection should be preserved
    const popGenre = page.getByTestId("genre-pop");
    await expect(popGenre).toHaveClass(/border-\[var\(--brand-pink\)\]/);

    // Go back to step 1
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("quiz-heading")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("quiz-heading")).toHaveText(/let's find your perfect karaoke songs/i);
  });

  test("selections persist through back/forward navigation", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Step 1: Go to step 2
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 5000 });

    // Step 2: Select genres and decades
    await page.getByTestId("genre-pop").click();
    await page.getByTestId("genre-rock").click();
    await page.getByTestId("decade-1980s").click();
    await page.getByRole("button", { name: /continue/i }).click();

    // Step 3: Artists
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /continue/i }).click();

    // Step 4: Preferences
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });
    await page.getByTestId("energy-high").click();
    await page.getByTestId("vocal-comfort-challenging").click();

    // Go back to step 2 and verify selections
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("music-taste-heading")).toBeVisible({ timeout: 5000 });

    // Check genres are still selected
    await expect(page.getByTestId("genre-pop")).toHaveClass(/border-\[var\(--brand-pink\)\]/);
    await expect(page.getByTestId("genre-rock")).toHaveClass(/border-\[var\(--brand-pink\)\]/);
    await expect(page.getByTestId("decade-1980s")).toHaveClass(/border-\[var\(--brand-pink\)\]/);

    // Go forward again and check preferences are preserved
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("energy-high")).toHaveClass(/border-\[var\(--brand-pink\)\]/);
    await expect(page.getByTestId("vocal-comfort-challenging")).toHaveClass(/border-\[var\(--brand-pink\)\]/);
  });

  test("can change selections after going back", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Go through to step 4
    await page.getByRole("button", { name: /get started/i }).click();
    await page.getByTestId("genre-pop").click();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });

    // Go back to step 2 and change selections
    await page.getByRole("button", { name: /back/i }).click();
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("music-taste-heading")).toBeVisible({ timeout: 5000 });

    // Deselect pop, select rock and country
    await page.getByTestId("genre-pop").click(); // Deselect
    await page.getByTestId("genre-rock").click();
    await page.getByTestId("genre-country").click();
    await page.getByTestId("decade-1990s").click();

    // Verify changes
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/2 genres selected/i);

    // Continue through and complete quiz
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("smart-artists-heading")).toBeVisible({ timeout: 10000 });

    // Finish quiz
    await page.getByRole("button", { name: /finish quiz/i }).click();

    // Email modal should appear - enter email and continue
    const emailInput = page.getByPlaceholder(/your@email.com/i);
    await expect(emailInput).toBeVisible({ timeout: 10000 });
    await emailInput.fill("test@example.com");
    await page.getByRole("button", { name: /continue/i }).click();

    await page.waitForURL(/\/recommendations/, { timeout: 10000 });
  });

  test("erratic back/forward navigation maintains state correctly", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Step 1 -> 2
    await page.getByRole("button", { name: /get started/i }).click();
    await expect(page.getByTestId("genre-grid")).toBeVisible({ timeout: 5000 });

    // Select a genre
    await page.getByTestId("genre-metal").click();
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/1 genre selected/i);

    // Go back to step 1, then forward again
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("quiz-heading")).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /get started/i }).click();

    // Metal should still be selected
    await expect(page.getByTestId("genre-metal")).toHaveClass(/border-\[var\(--brand-pink\)\]/);
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/1 genre selected/i);

    // Add more genres
    await page.getByTestId("genre-punk").click();
    await page.getByTestId("genre-grunge").click();
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/3 genres selected/i);

    // Continue to step 3
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("artists-you-know-heading")).toBeVisible({ timeout: 5000 });

    // Continue to step 4
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });

    // Select preferences
    await page.getByTestId("energy-high").click();

    // Jump back to step 2
    await page.getByRole("button", { name: /back/i }).click();
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("music-taste-heading")).toBeVisible({ timeout: 5000 });

    // All 3 genres should still be selected
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/3 genres selected/i);

    // Remove one
    await page.getByTestId("genre-punk").click();
    await expect(page.getByTestId("genre-selection-count")).toHaveText(/2 genres selected/i);

    // Go forward to step 5
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page.getByTestId("smart-artists-heading")).toBeVisible({ timeout: 10000 });

    // Verify step 5 has back button in the sticky bar and can navigate back
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.getByTestId("preferences-heading")).toBeVisible({ timeout: 5000 });

    // Energy preference should still be selected
    await expect(page.getByTestId("energy-high")).toHaveClass(/border-\[var\(--brand-pink\)\]/);
  });
});
