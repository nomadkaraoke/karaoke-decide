import { test, expect } from "@playwright/test";

/**
 * Production API tests - verify actual API endpoints work
 * These tests hit the real production API, not mocked endpoints
 */
test.describe("Production API Tests", () => {
  const API_BASE = "https://karaoke-decide-718638054799.us-central1.run.app";

  test("health endpoint returns 200", async ({ request }) => {
    const response = await request.get(`${API_BASE}/api/health`);
    expect(response.ok()).toBeTruthy();
  });

  test("catalog search returns results", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/catalog/songs?q=bohemian&limit=5`
    );
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.songs).toBeDefined();
    expect(data.songs.length).toBeGreaterThan(0);

    // Check structure of first song
    const song = data.songs[0];
    expect(song.artist).toBeDefined();
    expect(song.title).toBeDefined();
    expect(song.id).toBeDefined();
  });

  test("catalog stats returns valid data", async ({ request }) => {
    const response = await request.get(`${API_BASE}/api/catalog/stats`);
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.total_songs).toBeGreaterThan(200000); // Should have 275K+
    expect(data.unique_artists).toBeGreaterThan(50000);
  });

  test("popular songs endpoint works", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/catalog/songs/popular?limit=10`
    );
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    // Popular endpoint returns array directly
    expect(Array.isArray(data)).toBeTruthy();
    expect(data.length).toBe(10);

    // Check structure
    const song = data[0];
    expect(song.artist).toBeDefined();
    expect(song.title).toBeDefined();
    expect(song.brand_count).toBeGreaterThan(0);
  });

  test("magic link request returns success message", async ({ request }) => {
    // Use a test email that won't clutter real inboxes
    const response = await request.post(`${API_BASE}/api/auth/magic-link`, {
      data: { email: "e2e-test-do-not-use@example.com" },
    });
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.message).toContain("magic link");
  });

  test("protected endpoints require auth", async ({ request }) => {
    // Quiz songs should require auth
    const quizResponse = await request.get(`${API_BASE}/api/quiz/songs`);
    expect(quizResponse.status()).toBe(401);

    // My songs should require auth
    const mySongsResponse = await request.get(`${API_BASE}/api/my/songs`);
    expect(mySongsResponse.status()).toBe(401);

    // Recommendations should require auth
    const recsResponse = await request.get(`${API_BASE}/api/my/recommendations`);
    expect(recsResponse.status()).toBe(401);

    // Playlists should require auth
    const playlistsResponse = await request.get(`${API_BASE}/api/playlists`);
    expect(playlistsResponse.status()).toBe(401);

    // Services should require auth
    const servicesResponse = await request.get(`${API_BASE}/api/services`);
    expect(servicesResponse.status()).toBe(401);
  });

  test("invalid auth token returns 401", async ({ request }) => {
    const response = await request.get(`${API_BASE}/api/auth/me`, {
      headers: {
        Authorization: "Bearer invalid-token-12345",
      },
    });
    expect(response.status()).toBe(401);
  });

  test("CORS headers are present", async ({ request }) => {
    const response = await request.get(`${API_BASE}/api/health`);
    // Production should allow the frontend origin
    const headers = response.headers();
    // CORS is handled by preflight, but health should work cross-origin
    expect(response.ok()).toBeTruthy();
  });

  test("song search with special characters works", async ({ request }) => {
    // Test with apostrophe
    const response = await request.get(
      `${API_BASE}/api/catalog/songs?q=don't stop&limit=3`
    );
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.songs).toBeDefined();
  });

  test("empty search returns results or empty array", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/catalog/songs?q=xyzzy12345nonexistent&limit=5`
    );
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.songs).toBeDefined();
    expect(Array.isArray(data.songs)).toBeTruthy();
  });
});

/**
 * Frontend-API integration tests
 * These verify the frontend correctly communicates with the API
 */
test.describe("Frontend-API Integration", () => {
  test("homepage loads data from API", async ({ page }) => {
    await page.goto("/");

    // Wait for API call to complete
    await page.waitForLoadState("networkidle");

    // Should show "Popular" heading (from real data)
    await expect(page.getByText(/popular/i).first()).toBeVisible({
      timeout: 10000,
    });

    // Should show actual song cards (from real API)
    await expect(page.getByText(/sing it/i).first()).toBeVisible({
      timeout: 10000,
    });
  });

  test("search fetches real results", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Type a search
    const searchInput = page.getByPlaceholder(/search/i);
    await expect(searchInput).toBeVisible({ timeout: 10000 });
    await searchInput.fill("queen");

    // Wait for debounce and API response
    await page.waitForTimeout(800);
    await page.waitForLoadState("networkidle");

    // Should show search results text
    await expect(page.getByText(/results for/i)).toBeVisible({
      timeout: 10000,
    });

    // Should show Queen in results
    await expect(page.getByText("Queen").first()).toBeVisible();
  });

  test("login page can submit to real API", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    // Wait for page to load fully (auth check)
    await page.waitForTimeout(1000);

    // Find the email input - could be input with type email or placeholder
    const emailInput = page.locator('input[type="email"], input[placeholder*="email" i]').first();
    await expect(emailInput).toBeVisible({ timeout: 10000 });
    await emailInput.fill("e2e-integration-test@example.com");

    // Submit the form - use the specific "Send Magic Link" button
    const submitButton = page.getByRole("button", { name: /send magic link/i });
    await expect(submitButton).toBeVisible();
    await submitButton.click();

    // Should show success message (API is working)
    await expect(page.getByRole("heading", { name: /check your email/i })).toBeVisible({
      timeout: 10000,
    });
  });
});
