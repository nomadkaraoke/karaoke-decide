import { test, expect } from "@playwright/test";

/**
 * Tests for the improved onboarding flow.
 *
 * Key behaviors:
 * 1. New users clicking "Get Started" should go to /quiz (not /recommendations)
 * 2. Quiz page should be accessible and functional
 * 3. After completing quiz, users should go to /recommendations
 * 4. Recommendations page shows quiz prompt banner if quiz not completed
 * 5. Returning users who completed quiz go to /recommendations
 */
test.describe("Onboarding Flow", () => {
  test.beforeEach(async ({ context, page }) => {
    // Clear all cookies and storage to simulate a fresh user
    await context.clearCookies();
    // Clear localStorage and sessionStorage
    await page.goto("/");
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });
  });

  test("new user clicking Get Started goes to quiz", async ({ page }) => {
    // Visit landing page
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Verify we're on the landing page
    await expect(page.locator("h1")).toContainText("Find Your Perfect");

    // Click Get Started button
    const getStartedBtn = page.locator("button").filter({ hasText: /get started/i });
    await expect(getStartedBtn).toBeVisible();
    await getStartedBtn.click();

    // Wait for navigation
    await page.waitForURL(/\/quiz/, { timeout: 10000 });

    // Verify we're on the quiz page
    await expect(page.locator("[data-testid='quiz-heading'], h1")).toContainText(
      /what music do you like/i
    );
  });

  test("quiz page shows genre selection on step 1", async ({ page }) => {
    // Go directly to quiz
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Verify genre selection is visible
    const genreGrid = page.locator("[data-testid='genre-grid']");
    await expect(genreGrid).toBeVisible({ timeout: 10000 });

    // Verify some genres are present
    await expect(page.locator("[data-testid='genre-pop']")).toBeVisible();
    await expect(page.locator("[data-testid='genre-rock']")).toBeVisible();

    // Verify Continue button is disabled until genre is selected
    const continueBtn = page.locator("button").filter({ hasText: /continue/i });
    await expect(continueBtn).toBeDisabled();

    // Select a genre
    await page.locator("[data-testid='genre-rock']").click();

    // Continue button should now be enabled
    await expect(continueBtn).toBeEnabled();
  });

  test("quiz progress through all steps", async ({ page }) => {
    await page.goto("/quiz");
    await page.waitForLoadState("networkidle");

    // Step 1: Select genres
    await page.locator("[data-testid='genre-rock']").click();
    await page.locator("[data-testid='genre-pop']").click();
    await page.locator("button").filter({ hasText: /continue/i }).click();

    // Step 2: Preferences (optional)
    await expect(page.locator("[data-testid='preferences-heading'], h1")).toContainText(
      /preferences/i,
      { timeout: 5000 }
    );
    await page.locator("button").filter({ hasText: /continue/i }).click();

    // Step 3: Artists
    await expect(page.locator("[data-testid='artist-heading'], h1")).toContainText(
      /know any of these artists/i,
      { timeout: 5000 }
    );

    // Submit quiz
    await page.locator("button").filter({ hasText: /see recommendations/i }).click();

    // Should redirect to recommendations
    await page.waitForURL(/\/recommendations/, { timeout: 10000 });
  });

  test("recommendations page shows quiz banner when quiz not completed", async ({ page }) => {
    // Create a guest session by visiting landing page and clicking Get Started
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.locator("button").filter({ hasText: /get started/i }).click();

    // Wait to land on quiz
    await page.waitForURL(/\/quiz/, { timeout: 10000 });

    // Navigate directly to recommendations without completing quiz
    await page.goto("/recommendations");
    await page.waitForLoadState("networkidle");

    // Should see the quiz prompt banner
    const quizBanner = page.locator("text=Get personalized recommendations");
    await expect(quizBanner).toBeVisible({ timeout: 5000 });

    // Banner should have a "Take Quiz" button
    const takeQuizBtn = page.locator("button").filter({ hasText: /take quiz/i });
    await expect(takeQuizBtn).toBeVisible();
  });

  test("quiz banner click navigates to quiz", async ({ page }) => {
    // Setup: Go through the flow to get a session
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.locator("button").filter({ hasText: /get started/i }).click();
    await page.waitForURL(/\/quiz/, { timeout: 10000 });

    // Navigate to recommendations
    await page.goto("/recommendations");
    await page.waitForLoadState("networkidle");

    // Click Take Quiz button in banner
    await page.locator("button").filter({ hasText: /take quiz/i }).click();

    // Should be on quiz page
    await page.waitForURL(/\/quiz/, { timeout: 5000 });
  });
});

test.describe("Returning User Flow", () => {
  test("user who completed quiz goes to recommendations from landing", async ({ page }) => {
    // First, complete the quiz to set up state
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.locator("button").filter({ hasText: /get started/i }).click();
    await page.waitForURL(/\/quiz/, { timeout: 10000 });

    // Complete quiz quickly - wait for each step to be visible
    await page.locator("[data-testid='genre-rock']").click();
    await page.locator("button").filter({ hasText: /continue/i }).click();
    await expect(page.locator("[data-testid='preferences-heading'], h1")).toContainText(/preferences/i, { timeout: 5000 });
    await page.locator("button").filter({ hasText: /continue/i }).click();
    await expect(page.locator("[data-testid='artist-heading'], h1")).toContainText(/know any of these artists/i, { timeout: 5000 });
    await page.locator("button").filter({ hasText: /see recommendations/i }).click();

    // Wait to land on recommendations
    await page.waitForURL(/\/recommendations/, { timeout: 10000 });

    // Now go back to landing page
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Should redirect to recommendations (quiz already completed)
    await expect(page).toHaveURL(/\/recommendations/, { timeout: 10000 });
  });

  test("recommendations page has no quiz banner after quiz completed", async ({ page }) => {
    // Complete quiz
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.locator("button").filter({ hasText: /get started/i }).click();
    await page.waitForURL(/\/quiz/, { timeout: 10000 });

    await page.locator("[data-testid='genre-rock']").click();
    await page.locator("button").filter({ hasText: /continue/i }).click();
    await expect(page.locator("[data-testid='preferences-heading'], h1")).toContainText(/preferences/i, { timeout: 5000 });
    await page.locator("button").filter({ hasText: /continue/i }).click();
    await expect(page.locator("[data-testid='artist-heading'], h1")).toContainText(/know any of these artists/i, { timeout: 5000 });
    await page.locator("button").filter({ hasText: /see recommendations/i }).click();
    await page.waitForURL(/\/recommendations/, { timeout: 10000 });

    // Quiz banner should NOT be visible
    const quizBanner = page.locator("text=Get personalized recommendations");
    await expect(quizBanner).not.toBeVisible();
  });
});
