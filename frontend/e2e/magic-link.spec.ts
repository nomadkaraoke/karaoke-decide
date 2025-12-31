import { test, expect } from "@playwright/test";
import MailSlurp from "mailslurp-client";

// API key should be set via environment variable: MAILSLURP_API_KEY
const MAILSLURP_API_KEY = process.env.MAILSLURP_API_KEY || "";

/**
 * End-to-end magic link authentication test
 * Tests the complete flow: request magic link -> receive email -> click link -> authenticated
 */
test.describe("Magic Link E2E", () => {
  let mailslurp: MailSlurp;

  test.beforeAll(() => {
    mailslurp = new MailSlurp({ apiKey: MAILSLURP_API_KEY });
  });

  test("complete magic link authentication flow", async ({ page }) => {
    // Increase timeout for this test as it involves email delivery
    test.setTimeout(120000);

    // Step 1: Create a new Mailslurp inbox
    const inbox = await mailslurp.inboxController.createInboxWithDefaults();
    console.log(`Created inbox: ${inbox.emailAddress}`);

    // Step 2: Go to login page and request magic link
    await page.goto("/login");
    await expect(page.getByText("Welcome back")).toBeVisible();

    const emailInput = page.getByLabel(/email/i);
    await emailInput.fill(inbox.emailAddress!);

    const submitButton = page.getByRole("button", { name: /send magic link/i });
    await submitButton.click();

    // Step 3: Wait for success message
    await expect(page.getByText(/check your email/i)).toBeVisible({ timeout: 10000 });
    console.log("Magic link request successful");

    // Step 4: Wait for and fetch the email from Mailslurp
    console.log("Waiting for magic link email...");
    const email = await mailslurp.waitController.waitForLatestEmail({
      inboxId: inbox.id!,
      timeout: 60000,
      unreadOnly: true,
    });

    expect(email.subject).toContain("Sign in");
    console.log(`Received email with subject: ${email.subject}`);

    // Step 5: Extract magic link from email body
    const emailBody = email.body || "";
    const magicLinkMatch = emailBody.match(/https?:\/\/[^\s"<>]+\/auth\/verify\?token=[^\s"<>]+/);

    if (!magicLinkMatch) {
      console.log("Email body:", emailBody);
      throw new Error("Could not find magic link in email");
    }

    const magicLink = magicLinkMatch[0];
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

    // Cleanup: delete the inbox
    await mailslurp.inboxController.deleteInbox({ inboxId: inbox.id! });
  });
});
