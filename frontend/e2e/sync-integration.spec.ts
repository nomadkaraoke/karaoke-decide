import { test, expect } from "@playwright/test";

/**
 * Sync Integration Tests
 *
 * These tests verify the sync flow works end-to-end against production.
 * Requires: PROD_TEST_EMAIL and PROD_TEST_TOKEN environment variables
 * (or a stored auth state file)
 *
 * To run with auth:
 *   PROD_TEST_TOKEN=<jwt> npx playwright test e2e/sync-integration.spec.ts
 *
 * Or save auth state after manual login:
 *   npx playwright test e2e/sync-integration.spec.ts --project=chromium --headed
 *   (then use storageState in config)
 */
test.describe("Sync Integration Tests", () => {
  const API_BASE =
    process.env.API_BASE ||
    "https://karaoke-decide-718638054799.us-central1.run.app";
  const PROD_TEST_TOKEN = process.env.PROD_TEST_TOKEN;

  test.describe("API-level sync tests", () => {
    test.skip(!PROD_TEST_TOKEN, "Requires PROD_TEST_TOKEN env var");

    test("sync endpoint creates task successfully", async ({ request }) => {
      const response = await request.post(`${API_BASE}/api/services/sync`, {
        headers: {
          Authorization: `Bearer ${PROD_TEST_TOKEN}`,
        },
      });

      // Should return 202 Accepted with job_id
      expect(response.status()).toBe(202);
      const data = await response.json();
      expect(data.job_id).toBeDefined();
      expect(data.status).toBe("pending");

      console.log(`Sync job created: ${data.job_id}`);
    });

    test("sync status endpoint works", async ({ request }) => {
      const response = await request.get(
        `${API_BASE}/api/services/sync/status`,
        {
          headers: {
            Authorization: `Bearer ${PROD_TEST_TOKEN}`,
          },
        }
      );

      expect(response.ok()).toBeTruthy();
      const data = await response.json();
      expect(data.services).toBeDefined();
    });
  });

  test.describe("UI sync tests", () => {
    test.skip(!PROD_TEST_TOKEN, "Requires PROD_TEST_TOKEN env var");

    test.beforeEach(async ({ page }) => {
      // Set auth token in localStorage before navigating
      await page.goto(process.env.BASE_URL || "https://decide.nomadkaraoke.com");
      await page.evaluate((token) => {
        localStorage.setItem("karaoke_decide_token", token);
      }, PROD_TEST_TOKEN);
    });

    test("sync button triggers actual sync", async ({ page }) => {
      // Navigate to services page
      await page.goto(
        `${process.env.BASE_URL || "https://decide.nomadkaraoke.com"}/services`
      );
      await page.waitForLoadState("networkidle");

      // Look for sync button
      const syncButton = page.getByRole("button", { name: /sync now/i });

      // If no services connected, sync button won't appear
      const syncButtonVisible = (await syncButton.count()) > 0;
      if (!syncButtonVisible) {
        console.log("No connected services - skipping sync test");
        test.skip();
        return;
      }

      // Click sync and verify response
      const [response] = await Promise.all([
        page.waitForResponse((r) => r.url().includes("/api/services/sync")),
        syncButton.click(),
      ]);

      // Should not get 403 IAM error
      expect(response.status()).not.toBe(403);

      // Should get 202 Accepted
      expect(response.status()).toBe(202);

      // UI should show syncing state
      await expect(page.getByText(/syncing/i)).toBeVisible({ timeout: 5000 });
    });
  });
});
