import { test, expect } from "@playwright/test";

/**
 * Admin E2E tests - verify admin functionality against production
 *
 * These tests require a valid admin JWT token set via ADMIN_TOKEN env var.
 * The token should be for an admin user (e.g., admin@nomadkaraoke.com).
 *
 * Run with:
 *   ADMIN_TOKEN=<jwt> BASE_URL=https://decide.nomadkaraoke.com npx playwright test admin.spec.ts
 */

const API_BASE = "https://karaoke-decide-718638054799.us-central1.run.app";

test.describe("Admin API Tests", () => {
  // Skip all tests if no admin token is provided
  test.beforeEach(async () => {
    if (!process.env.ADMIN_TOKEN) {
      test.skip();
    }
  });

  const getAuthHeaders = () => ({
    Authorization: `Bearer ${process.env.ADMIN_TOKEN}`,
  });

  test("admin/stats endpoint returns dashboard statistics", async ({
    request,
  }) => {
    const response = await request.get(`${API_BASE}/api/admin/stats`, {
      headers: getAuthHeaders(),
    });

    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // Verify response structure
    expect(data.users).toBeDefined();
    expect(data.users.total).toBeGreaterThanOrEqual(0);
    expect(data.users.verified).toBeGreaterThanOrEqual(0);
    expect(data.users.guests).toBeGreaterThanOrEqual(0);
    expect(data.users.active_7d).toBeGreaterThanOrEqual(0);

    expect(data.sync_jobs).toBeDefined();
    expect(data.sync_jobs.total).toBeGreaterThanOrEqual(0);
    expect(data.sync_jobs.pending).toBeGreaterThanOrEqual(0);
    expect(data.sync_jobs.in_progress).toBeGreaterThanOrEqual(0);
    expect(data.sync_jobs.completed).toBeGreaterThanOrEqual(0);
    expect(data.sync_jobs.failed).toBeGreaterThanOrEqual(0);

    expect(data.services).toBeDefined();
    expect(data.services.spotify_connected).toBeGreaterThanOrEqual(0);
    expect(data.services.lastfm_connected).toBeGreaterThanOrEqual(0);
  });

  test("admin/users endpoint lists users", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/admin/users?limit=5&filter=all`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // Verify response structure
    expect(data.users).toBeDefined();
    expect(Array.isArray(data.users)).toBeTruthy();
    expect(data.total).toBeGreaterThanOrEqual(0);
    expect(data.limit).toBe(5);
    expect(data.offset).toBe(0);

    // If we have users, verify structure
    if (data.users.length > 0) {
      const user = data.users[0];
      expect(user.id).toBeDefined();
      expect(typeof user.is_guest).toBe("boolean");
      expect(typeof user.is_admin).toBe("boolean");
      expect(user.created_at).toBeDefined();
    }
  });

  test("admin/users with verified filter", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/admin/users?limit=5&filter=verified`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // All returned users should be verified (not guests)
    for (const user of data.users) {
      expect(user.is_guest).toBe(false);
    }
  });

  test("admin/users with guests filter", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/admin/users?limit=5&filter=guests`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // All returned users should be guests
    for (const user of data.users) {
      expect(user.is_guest).toBe(true);
    }
  });

  test("admin/sync-jobs endpoint lists sync jobs", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/admin/sync-jobs?limit=5`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // Verify response structure
    expect(data.jobs).toBeDefined();
    expect(Array.isArray(data.jobs)).toBeTruthy();
    expect(data.total).toBeGreaterThanOrEqual(0);
    expect(data.limit).toBe(5);
    expect(data.offset).toBe(0);

    // If we have jobs, verify structure
    if (data.jobs.length > 0) {
      const job = data.jobs[0];
      expect(job.id).toBeDefined();
      expect(job.user_id).toBeDefined();
      expect(job.status).toBeDefined();
      expect(job.created_at).toBeDefined();
    }
  });

  test("admin/sync-jobs with status filter", async ({ request }) => {
    const response = await request.get(
      `${API_BASE}/api/admin/sync-jobs?limit=5&status=completed`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // All returned jobs should have completed status
    for (const job of data.jobs) {
      expect(job.status).toBe("completed");
    }
  });

  test("admin/users/{id} returns user detail", async ({ request }) => {
    // First get a user from the list
    const listResponse = await request.get(
      `${API_BASE}/api/admin/users?limit=1&filter=all`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(listResponse.ok()).toBeTruthy();
    const listData = await listResponse.json();

    if (listData.users.length === 0) {
      test.skip();
      return;
    }

    const userId = listData.users[0].id;

    // Now get the user detail
    const detailResponse = await request.get(
      `${API_BASE}/api/admin/users/${userId}`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(detailResponse.ok()).toBeTruthy();
    const detail = await detailResponse.json();

    // Verify response structure
    expect(detail.id).toBe(userId);
    expect(detail.services).toBeDefined();
    expect(Array.isArray(detail.services)).toBeTruthy();
    expect(detail.sync_jobs).toBeDefined();
    expect(Array.isArray(detail.sync_jobs)).toBeTruthy();
    expect(detail.data_summary).toBeDefined();
    expect(detail.data_summary.artists_count).toBeGreaterThanOrEqual(0);
    expect(detail.data_summary.songs_count).toBeGreaterThanOrEqual(0);
    expect(detail.data_summary.playlists_count).toBeGreaterThanOrEqual(0);
  });

  test("admin/sync-jobs/{id} returns job detail", async ({ request }) => {
    // First get a job from the list
    const listResponse = await request.get(
      `${API_BASE}/api/admin/sync-jobs?limit=1`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(listResponse.ok()).toBeTruthy();
    const listData = await listResponse.json();

    if (listData.jobs.length === 0) {
      test.skip();
      return;
    }

    const jobId = listData.jobs[0].id;

    // Now get the job detail
    const detailResponse = await request.get(
      `${API_BASE}/api/admin/sync-jobs/${jobId}`,
      {
        headers: getAuthHeaders(),
      }
    );

    expect(detailResponse.ok()).toBeTruthy();
    const detail = await detailResponse.json();

    // Verify response structure
    expect(detail.id).toBe(jobId);
    expect(detail.user_id).toBeDefined();
    expect(detail.status).toBeDefined();
    expect(detail.created_at).toBeDefined();
    expect(detail.results).toBeDefined();
    expect(Array.isArray(detail.results)).toBeTruthy();
  });

  test("admin endpoints require admin access", async ({ request }) => {
    // Test with no token
    const noAuthResponse = await request.get(`${API_BASE}/api/admin/stats`);
    expect(noAuthResponse.status()).toBe(401);

    // Test with invalid token
    const invalidResponse = await request.get(`${API_BASE}/api/admin/stats`, {
      headers: { Authorization: "Bearer invalid-token" },
    });
    expect(invalidResponse.status()).toBe(401);
  });
});

test.describe("Admin Frontend Tests", () => {
  // Skip all tests if no admin token is provided
  test.beforeEach(async () => {
    if (!process.env.ADMIN_TOKEN) {
      test.skip();
    }
  });

  test("admin dashboard loads for admin user", async ({ page, context }) => {
    // Set the auth token in localStorage before navigating
    await context.addCookies([
      {
        name: "auth_token",
        value: process.env.ADMIN_TOKEN!,
        domain: "decide.nomadkaraoke.com",
        path: "/",
      },
    ]);

    // Also set in localStorage via page
    await page.goto("https://decide.nomadkaraoke.com");
    await page.evaluate((token) => {
      localStorage.setItem("auth_token", token);
    }, process.env.ADMIN_TOKEN);

    // Navigate to admin
    await page.goto("https://decide.nomadkaraoke.com/admin");
    await page.waitForLoadState("networkidle");

    // Should show admin dashboard content (not redirect away)
    // Check for presence of admin-specific elements
    await expect(
      page.getByRole("heading", { name: /admin/i }).first()
    ).toBeVisible({
      timeout: 15000,
    });
  });

  test("admin navigation shows admin link", async ({ page }) => {
    // Set the auth token
    await page.goto("https://decide.nomadkaraoke.com");
    await page.evaluate((token) => {
      localStorage.setItem("auth_token", token);
    }, process.env.ADMIN_TOKEN);

    // Reload to apply auth
    await page.reload();
    await page.waitForLoadState("networkidle");

    // Admin link should be visible in navigation
    await expect(page.getByRole("link", { name: /admin/i })).toBeVisible({
      timeout: 15000,
    });
  });
});
