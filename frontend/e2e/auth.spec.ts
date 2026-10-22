import { test, expect } from "@playwright/test";

/**
 * Authentication flow tests
 */
test.describe("Authentication", () => {
  test.beforeEach(async ({ page }) => {
    // Clear any stored tokens before each test
    await page.goto("/");
    await page.evaluate(() => localStorage.clear());
  });

  test("login page renders correctly", async ({ page }) => {
    await page.goto("/login");

    // Check heading
    await expect(page.getByText("Welcome back")).toBeVisible();

    // Check email input
    await expect(page.getByLabel(/email/i)).toBeVisible();

    // Check submit button
    await expect(page.getByTestId("login-send-code")).toBeVisible();

    // Check alternative action
    await expect(page.getByRole("button", { name: /browse songs/i })).toBeVisible();
  });

  test("can enter email on login page", async ({ page }) => {
    await page.goto("/login");

    const emailInput = page.getByLabel(/email/i);
    await emailInput.fill("test@example.com");

    await expect(emailInput).toHaveValue("test@example.com");
  });

  test("login page validates email format", async ({ page }) => {
    await page.goto("/login");

    const emailInput = page.getByLabel(/email/i);
    const submitButton = page.getByTestId("login-send-code");

    // Try to submit with invalid email - HTML5 validation should prevent
    await emailInput.fill("not-an-email");
    await submitButton.click();

    // The form should not have been submitted (still on the email step)
    await expect(page.getByTestId("login-code")).not.toBeVisible();
  });

  test("protected routes redirect to login when not authenticated", async ({ page }) => {
    // Try to access my-songs without auth
    await page.goto("/my-songs");

    // Should redirect to login
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
  });

  test("navigation shows sign in button when not authenticated", async ({ page }) => {
    await page.goto("/");

    // Wait for auth check to complete
    await page.waitForTimeout(1000);

    // Desktop navigation should show Sign In
    await expect(page.getByRole("link", { name: /sign in/i }).first()).toBeVisible();
  });

  test("browse songs link on login page navigates to home", async ({ page }) => {
    await page.goto("/login");

    await page.getByRole("button", { name: /browse songs/i }).click();

    await expect(page).toHaveURL("/");
  });
});
