import { test, expect } from "@playwright/test";

/**
 * Known Songs page tests
 *
 * Tests for the page where users can search for and add songs
 * they already know they like singing.
 */
test.describe("Known Songs Page", () => {
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

  test("known-songs page structure is correct", async ({ page }) => {
    // Mock the known songs endpoint
    await page.route("**/api/known-songs*", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            songs: [
              {
                id: "user_test123:1",
                song_id: "1",
                artist: "Queen",
                title: "Bohemian Rhapsody",
                source: "known_songs",
                is_saved: true,
                created_at: "2024-01-01T12:00:00Z",
                updated_at: "2024-01-01T12:00:00Z",
              },
              {
                id: "user_test123:2",
                song_id: "2",
                artist: "Journey",
                title: "Don't Stop Believin'",
                source: "known_songs",
                is_saved: true,
                created_at: "2024-01-02T12:00:00Z",
                updated_at: "2024-01-02T12:00:00Z",
              },
            ],
            total: 2,
            page: 1,
            per_page: 20,
          }),
        });
      }
    });

    await page.goto("/known-songs");
    await page.waitForLoadState("networkidle");

    // Check heading
    await expect(page.getByRole("heading", { name: "Songs I Know" })).toBeVisible({ timeout: 10000 });

    // Check description
    await expect(page.getByText("Add songs you already love singing")).toBeVisible();

    // Check search input
    await expect(page.getByTestId("song-search-input")).toBeVisible();

    // Check known songs count
    await expect(page.getByText("2 songs")).toBeVisible();

    // Check first song appears
    await expect(page.getByText("Bohemian Rhapsody")).toBeVisible();
    await expect(page.getByText("Queen")).toBeVisible();
  });

  test("known-songs shows empty state when no songs", async ({ page }) => {
    // Mock empty known songs response
    await page.route("**/api/known-songs*", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            songs: [],
            total: 0,
            page: 1,
            per_page: 20,
          }),
        });
      }
    });

    await page.goto("/known-songs");
    await page.waitForLoadState("networkidle");

    // Check empty state message
    await expect(page.getByText("No known songs yet")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Search above to add songs")).toBeVisible();
  });

  test("can search for songs in catalog", async ({ page }) => {
    // Mock known songs endpoint (empty initially)
    await page.route("**/api/known-songs*", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            songs: [],
            total: 0,
            page: 1,
            per_page: 20,
          }),
        });
      }
    });

    // Mock catalog search endpoint
    await page.route("**/api/catalog/songs*", async (route) => {
      const url = route.request().url();
      if (url.includes("q=queen")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            songs: [
              {
                id: 1,
                artist: "Queen",
                title: "Bohemian Rhapsody",
                brands: ["karafun", "singa", "karaoke-version"],
                brand_count: 3,
                is_popular: true,
              },
              {
                id: 2,
                artist: "Queen",
                title: "We Will Rock You",
                brands: ["karafun", "singa"],
                brand_count: 2,
                is_popular: false,
              },
            ],
            total: 2,
            page: 1,
            per_page: 10,
            has_more: false,
          }),
        });
      }
    });

    await page.goto("/known-songs");
    await page.waitForLoadState("networkidle");

    // Type in search
    const searchInput = page.getByTestId("song-search-input");
    await searchInput.fill("queen");

    // Wait for search results
    await expect(page.getByText("Search results (2)")).toBeVisible({ timeout: 10000 });

    // Check search results appear
    await expect(page.getByTestId("search-result-item").first()).toBeVisible();
    await expect(page.getByText("Bohemian Rhapsody")).toBeVisible();
    await expect(page.getByText("We Will Rock You")).toBeVisible();
  });

  test("can add a song from search results", async ({ page }) => {
    // Track added songs
    let addedSong = false;

    // Mock known songs endpoint
    await page.route("**/api/known-songs", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            songs: addedSong
              ? [
                  {
                    id: "user_test123:1",
                    song_id: "1",
                    artist: "Queen",
                    title: "Bohemian Rhapsody",
                    source: "known_songs",
                    is_saved: true,
                    created_at: "2024-01-01T12:00:00Z",
                    updated_at: "2024-01-01T12:00:00Z",
                  },
                ]
              : [],
            total: addedSong ? 1 : 0,
            page: 1,
            per_page: 20,
          }),
        });
      } else if (route.request().method() === "POST") {
        addedSong = true;
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            added: true,
            song_id: "1",
            artist: "Queen",
            title: "Bohemian Rhapsody",
            already_existed: false,
          }),
        });
      }
    });

    // Mock catalog search
    await page.route("**/api/catalog/songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [
            {
              id: 1,
              artist: "Queen",
              title: "Bohemian Rhapsody",
              brands: ["karafun", "singa"],
              brand_count: 2,
              is_popular: true,
            },
          ],
          total: 1,
          page: 1,
          per_page: 10,
          has_more: false,
        }),
      });
    });

    await page.goto("/known-songs");
    await page.waitForLoadState("networkidle");

    // Search for a song
    await page.getByTestId("song-search-input").fill("queen");

    // Wait for search results
    await page.waitForSelector('[data-testid="search-result-item"]');

    // Click Add button
    await page.getByTestId("add-song-button").first().click();

    // Button should change to "Added"
    await expect(page.getByText("Added")).toBeVisible({ timeout: 10000 });
  });

  test("can remove a known song", async ({ page }) => {
    // Track removed state
    let songRemoved = false;

    // Mock known songs endpoint
    await page.route("**/api/known-songs*", async (route) => {
      const url = route.request().url();
      const method = route.request().method();

      if (method === "GET" && !url.includes("/1")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            songs: songRemoved
              ? []
              : [
                  {
                    id: "user_test123:1",
                    song_id: "1",
                    artist: "Queen",
                    title: "Bohemian Rhapsody",
                    source: "known_songs",
                    is_saved: true,
                    created_at: "2024-01-01T12:00:00Z",
                    updated_at: "2024-01-01T12:00:00Z",
                  },
                ],
            total: songRemoved ? 0 : 1,
            page: 1,
            per_page: 20,
          }),
        });
      } else if (method === "DELETE") {
        songRemoved = true;
        await route.fulfill({
          status: 204,
          body: "",
        });
      }
    });

    await page.goto("/known-songs");
    await page.waitForLoadState("networkidle");

    // Verify song is present
    await expect(page.getByTestId("known-song-item")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Bohemian Rhapsody")).toBeVisible();

    // Click remove button
    await page.getByTestId("remove-song-button").click();

    // Song should be removed from list
    await expect(page.getByTestId("known-song-item")).not.toBeVisible({ timeout: 10000 });
  });

  test("navigation includes Add Songs link", async ({ page }) => {
    // Mock known songs endpoint
    await page.route("**/api/known-songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [],
          total: 0,
          page: 1,
          per_page: 20,
        }),
      });
    });

    await page.goto("/known-songs");
    await page.waitForLoadState("networkidle");

    // On mobile, need to open the menu first
    const isMobile = await page.viewportSize();
    if (isMobile && isMobile.width < 768) {
      // Open mobile menu if present
      const menuButton = page.getByRole("button", { name: /menu|open/i });
      if (await menuButton.isVisible()) {
        await menuButton.click();
      }
    }

    // Check navigation has "Add Songs" link (visible in nav or mobile menu)
    const addSongsLink = page.getByRole("link", { name: /add songs/i });
    await expect(addSongsLink.first()).toBeVisible({ timeout: 10000 });
  });

  test("shows link to recommendations", async ({ page }) => {
    // Mock known songs endpoint
    await page.route("**/api/known-songs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          songs: [],
          total: 0,
          page: 1,
          per_page: 20,
        }),
      });
    });

    await page.goto("/known-songs");
    await page.waitForLoadState("networkidle");

    // Check for recommendations link in footer
    await expect(
      page.getByRole("link", { name: /view recommendations/i })
    ).toBeVisible({ timeout: 10000 });
  });
});
