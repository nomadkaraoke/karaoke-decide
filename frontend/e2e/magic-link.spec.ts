import { test, expect } from "@playwright/test";
import {
  createTestMailClient,
  generateTestTag,
  getEmailAddress,
  waitForEmail,
  extractUrlFromEmail,
} from "./utils/testmail";

/**
 * End-to-end magic link authentication test
 * Tests the complete flow: request magic link -> receive email -> click link -> authenticated
 *
 * Required environment variables:
 * - TESTMAIL_API_KEY: Your TestMail.app API key
 * - TESTMAIL_NAMESPACE: Your TestMail.app namespace
 */
test.describe("Magic Link E2E", () => {
  const testMailClient = createTestMailClient();
  let authToken: string | null = null;

  test.skip(!testMailClient, "Requires TESTMAIL_API_KEY and TESTMAIL_NAMESPACE env vars");

  test.afterEach(async ({ request, page }) => {
    // Clean up test user to prevent database clutter
    if (!authToken) {
      try {
        authToken = await page.evaluate(() => localStorage.getItem("karaoke_decide_token"));
      } catch {
        // Page may have been closed
      }
    }

    if (authToken) {
      try {
        const response = await request.delete("https://karaoke-decide-718638054799.us-central1.run.app/api/auth/me", {
          headers: { Authorization: `Bearer ${authToken}` },
        });
        if (response.ok()) {
          console.log("✓ Test user cleaned up successfully");
        } else {
          console.log(`⚠ Failed to clean up test user: ${response.status()}`);
        }
      } catch (error) {
        console.log(`⚠ Error cleaning up test user: ${error}`);
      }
    }
  });

  test("complete magic link authentication flow", async ({ page }) => {
    // Increase timeout for this test as it involves email delivery
    test.setTimeout(120000);

    // Step 1: Generate a unique tag for this test
    const tag = generateTestTag();
    const testEmail = getEmailAddress(testMailClient!.namespace, tag);
    console.log(`Test email: ${testEmail}`);

    // Step 2: Go to login page and request magic link
    await page.goto("/login");
    await expect(page.getByText("Welcome back")).toBeVisible();

    const emailInput = page.getByLabel(/email/i);
    await emailInput.fill(testEmail);

    const submitButton = page.getByRole("button", { name: /send magic link/i });
    await submitButton.click();

    // Step 3: Wait for success message
    await expect(page.getByText(/check your email/i)).toBeVisible({ timeout: 10000 });
    console.log("Magic link request successful");

    // Step 4: Wait for email to arrive using TestMail.app's livequery
    console.log("Waiting for magic link email...");
    const email = await waitForEmail(testMailClient!, tag, 60000);

    expect(email.subject.toLowerCase()).toContain("sign in");
    console.log(`Received email with subject: ${email.subject}`);

    // Step 5: Extract magic link from email body
    const magicLinkPattern = /https?:\/\/[^\s"<>]+\/auth\/verify\?token=[^\s"<>]+/;
    const magicLink = extractUrlFromEmail(email, magicLinkPattern);

    if (!magicLink) {
      console.log("Email HTML:", email.html);
      console.log("Email text:", email.text);
      throw new Error("Could not find magic link in email");
    }

    console.log(`Found magic link: ${magicLink}`);

    // Step 6: Visit the magic link
    await page.goto(magicLink);

    // Step 7: Verify we're authenticated - should redirect to home or show authenticated state
    // Wait for the verification to complete
    await page.waitForURL((url) => !url.pathname.includes("/auth/verify"), { timeout: 15000 });

    console.log(`Redirected to: ${page.url()}`);

    // Check that we're now authenticated by looking for user-specific UI
    // The navigation should show user menu or profile link instead of "Sign In"
    await expect(page.getByRole("link", { name: /sign in/i })).not.toBeVisible({ timeout: 5000 });

    // Or check that we can access a protected page
    await page.goto("/my-songs");
    await expect(page).not.toHaveURL("/login", { timeout: 5000 });

    console.log("Magic link authentication successful!");

    // Store token for cleanup in afterEach
    authToken = await page.evaluate(() => localStorage.getItem("karaoke_decide_token"));
  });
});
