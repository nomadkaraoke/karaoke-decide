import { test, expect } from '@playwright/test';

/**
 * Smoke tests for production verification.
 * These run after deployment to ensure critical functionality works.
 */
test.describe('Production Smoke Tests', () => {
  test('homepage loads successfully', async ({ page }) => {
    await page.goto('/');

    // Check main heading is visible
    await expect(page.getByText('Nomad')).toBeVisible();
    await expect(page.getByText('Karaoke')).toBeVisible();

    // Check search input is present
    await expect(page.getByPlaceholder(/search/i)).toBeVisible();
  });

  test('search input is functional', async ({ page }) => {
    await page.goto('/');

    const searchInput = page.getByPlaceholder(/search/i);
    await expect(searchInput).toBeVisible();

    // Type a search query
    await searchInput.fill('bohemian');

    // Input should have the value
    await expect(searchInput).toHaveValue('bohemian');
  });

  test('search returns results', async ({ page }) => {
    await page.goto('/');

    // Search for a popular song
    await page.getByPlaceholder(/search/i).fill('queen');

    // Wait for results to load (debounced search)
    await page.waitForTimeout(500);

    // Should show results section
    await expect(page.getByText(/results for/i)).toBeVisible({ timeout: 10000 });
  });

  test('popular songs load on homepage', async ({ page }) => {
    await page.goto('/');

    // Wait for initial load
    await page.waitForLoadState('networkidle');

    // Should show "Popular Karaoke Songs" or similar
    await expect(
      page.getByText(/popular/i).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test('no console errors on homepage', async ({ page }) => {
    const errors: string[] = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Filter out known acceptable errors (like favicon 404)
    const criticalErrors = errors.filter(
      (err) => !err.includes('favicon') && !err.includes('404')
    );

    expect(criticalErrors).toHaveLength(0);
  });

  test('page is responsive on mobile', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');

    // Search should still be visible and usable on mobile
    await expect(page.getByPlaceholder(/search/i)).toBeVisible();

    // Header should be visible
    await expect(page.getByText('Nomad')).toBeVisible();
  });

  test('API is responding', async ({ page }) => {
    await page.goto('/');

    // Search triggers API call
    await page.getByPlaceholder(/search/i).fill('test');

    // Wait for network idle (API response received)
    await page.waitForLoadState('networkidle');

    // Page should not show error state
    await expect(page.getByText(/failed to load/i)).not.toBeVisible();
  });

  test('song cards have sing button', async ({ page }) => {
    await page.goto('/');

    // Wait for songs to load
    await page.waitForLoadState('networkidle');

    // At least one "Sing it!" button should be visible
    await expect(page.getByText(/sing it/i).first()).toBeVisible({ timeout: 10000 });
  });
});
