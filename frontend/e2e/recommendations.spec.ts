import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Recommendations page tests (v2 endpoint, mocked).
 */

function song(key: string, artist: string, title: string, overrides: Record<string, unknown> = {}) {
  return {
    song_key: key,
    artist,
    title,
    reason: "You know this",
    reason_type: "known_song",
    reason_artist: null,
    score: 0.9,
    has_karaoke_version: true,
    karaoke: { community: false, commercial: true, karafun: false, brand_count: 8 },
    facets: {
      decade: "1970s",
      genres: ["pop"],
      mood: null,
      energy: null,
      lead_gender: null,
      duration_ms: 230000,
      popularity: 85,
      is_classic: false,
      explicit: false,
    },
    ...overrides,
  };
}

function section(id: string, songs: unknown[], titleParams: Record<string, string> = {}) {
  const key = id.startsWith("because_") ? "because" : id;
  return {
    id,
    title_key: `recs.section.${key}.title`,
    reason_key: `recs.section.${key}.reason`,
    title_params: titleParams,
    songs,
  };
}

function v2Body(sections: unknown[], next_cursor: string | null = null) {
  return {
    sections,
    filters_applied: {},
    next_cursor,
    has_profile: true,
    catalog_run: "test",
  };
}

const DEFAULT_SECTIONS = [
  section("songs_you_know", [song("abba|dancing queen", "ABBA", "Dancing Queen")]),
  section(
    "because_muse",
    [song("coldplay|yellow", "Coldplay", "Yellow", { reason_type: "similar_artist", reason_artist: "Muse" })],
    { artist: "Muse" }
  ),
  section("make_it", [
    song("x|made up", "Invented Band", "Made Up Song", {
      reason_type: "make_it",
      has_karaoke_version: false,
      karaoke: { community: false, commercial: false, karafun: false, brand_count: 0 },
    }),
  ]),
];

/** Capture request URLs to the v2 endpoint and answer with the given handler. */
async function mockV2(page: Page, handler?: (route: Route, url: URL) => Promise<void> | void) {
  const urls: URL[] = [];
  await page.route("**/api/my/recommendations/v2*", async (route) => {
    const url = new URL(route.request().url());
    urls.push(url);
    if (handler) return handler(route, url);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(v2Body(DEFAULT_SECTIONS)),
    });
  });
  return urls;
}

test.describe("Recommendations Page (v2)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => {
      localStorage.setItem("karaoke_decide_token", "test-token-12345");
    });
    await page.route("**/api/quiz/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ completed: true, completed_at: null, songs_known_count: 5 }),
      });
    });
    await page.route("**/api/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "user_test123", email: "test@example.com", display_name: "Test User", is_guest: false }),
      });
    });
  });

  test("renders sections in response order with reasons", async ({ page }) => {
    await mockV2(page);
    await page.goto("/en/recommendations");

    const headings = page.locator("h2");
    await expect(headings.first()).toBeVisible({ timeout: 10000 });
    await expect(headings).toHaveText([/Songs you know/, /Because you like Muse/, /Make it/]);
    await expect(page.getByText("Dancing Queen")).toBeVisible();
    await expect(page.getByText("Like Muse", { exact: true })).toBeVisible();
    // Generate-only row links to gen with artist/title
    const gen = page.getByRole("link", { name: /generate/i }).first();
    await expect(gen).toHaveAttribute("href", /gen\.nomadkaraoke\.com\?artist=Invented%20Band&title=Made%20Up%20Song/);
  });

  const cases: Array<{ name: string; select: string; value: string; expected: Record<string, string | null> }> = [
    { name: "hidden gems", select: "Popularity", value: "hidden-gems", expected: { popularity_max: "29", popularity: null } },
    { name: "chart toppers", select: "Popularity", value: "chart-toppers", expected: { popularity: "70", popularity_max: null } },
    { name: "somewhat known", select: "Popularity", value: "somewhat-known", expected: { popularity: "30", popularity_max: "49" } },
    { name: "karaoke ready", select: "Show", value: "karaoke", expected: { has_karaoke: "yes" } },
    { name: "generate only", select: "Show", value: "generate", expected: { has_karaoke: "make" } },
    { name: "duration", select: "Duration", value: "medium", expected: { duration: "medium" } },
    { name: "sort", select: "Sort by", value: "newest", expected: { sort: "newest" } },
    { name: "library", select: "Karaoke library", value: "karafun", expected: { library: "karafun" } },
  ];

  for (const c of cases) {
    test(`control sends expected query: ${c.name}`, async ({ page }) => {
      const urls = await mockV2(page);
      await page.goto("/en/recommendations");
      await expect(page.locator("h2").first()).toBeVisible({ timeout: 10000 });

      const before = urls.length;
      await page.locator("div", { has: page.locator(`label:text-is("${c.select}")`) }).last()
        .locator("select").selectOption(c.value);
      await expect.poll(() => urls.length).toBeGreaterThan(before);

      const last = urls[urls.length - 1];
      for (const [k, v] of Object.entries(c.expected)) {
        expect(last.searchParams.get(k)).toBe(v);
      }
    });
  }

  test("checkboxes send explicit=exclude and classic=true", async ({ page }) => {
    const urls = await mockV2(page);
    await page.goto("/en/recommendations");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 10000 });

    await page.getByLabel(/hide explicit/i).check();
    await expect.poll(() => urls[urls.length - 1].searchParams.get("explicit")).toBe("exclude");
    await page.getByLabel(/classics only/i).check();
    await expect.poll(() => urls[urls.length - 1].searchParams.get("classic")).toBe("true");
  });

  test("503 warming_up shows friendly state and retries", async ({ page }) => {
    let calls = 0;
    const urls = await mockV2(page, async (route) => {
      calls += 1;
      if (calls === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          headers: { "Retry-After": "30" },
          body: JSON.stringify({ detail: "warming_up" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(v2Body(DEFAULT_SECTIONS)),
      });
    });
    await page.goto("/en/recommendations");

    await expect(page.getByTestId("recs-warming")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/getting your picks ready/i)).toBeVisible();

    await page.getByRole("button", { name: /try again/i }).click();
    await expect(page.getByText("Dancing Queen")).toBeVisible();
    expect(urls.length).toBe(2);
    await expect(page.getByTestId("recs-warming")).toHaveCount(0);
  });

  test("empty result with filters offers clear filters", async ({ page }) => {
    await mockV2(page, async (route, url) => {
      const sections = url.searchParams.get("classic") ? [] : DEFAULT_SECTIONS;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(v2Body(sections)) });
    });
    await page.goto("/en/recommendations");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 10000 });

    await page.getByLabel(/classics only/i).check();
    await expect(page.getByText(/no picks match these filters/i)).toBeVisible();
    await page.getByRole("button", { name: /^clear all/i }).last().click();
    await expect(page.getByText("Dancing Queen")).toBeVisible();
  });

  test("load more appends the next page", async ({ page }) => {
    await mockV2(page, async (route, url) => {
      const cursor = url.searchParams.get("cursor");
      const body = cursor
        ? v2Body([section("all", [song("b|second", "Invented Band", "Second Page")])])
        : v2Body([section("all", [song("a|first", "Invented Band", "First Page")])], "cur1");
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/en/recommendations");
    await page.locator('select#rec-sort').selectOption("az");
    await expect(page.getByText("First Page")).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /load more/i }).click();
    await expect(page.getByText("Second Page")).toBeVisible();
    await expect(page.getByText("First Page")).toBeVisible();
    await expect(page.getByRole("button", { name: /load more/i })).toHaveCount(0);
  });

  test("no data at all and no filters shows the onboarding empty state", async ({ page }) => {
    await mockV2(page, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(v2Body([])) });
    });
    await page.goto("/en/recommendations");
    await expect(page.getByText(/no recommendations yet/i)).toBeVisible({ timeout: 10000 });
  });
});
