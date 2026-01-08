import { chromium } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const PROD_URL = 'https://decide.nomadkaraoke.com';
const SCREENSHOTS_DIR = './screenshots/homepage';

async function captureScreenshots() {
  // Ensure screenshots directory exists
  if (!fs.existsSync(SCREENSHOTS_DIR)) {
    fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
  }

  const browser = await chromium.launch({ headless: true });

  // Desktop viewport
  const desktopContext = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  const desktopPage = await desktopContext.newPage();

  // Mobile viewport
  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const mobilePage = await mobileContext.newPage();

  console.log('Capturing Quiz page (Step 1 - Genres)...');
  await desktopPage.goto(`${PROD_URL}/quiz`, { waitUntil: 'networkidle' });
  await desktopPage.waitForTimeout(1500);
  await desktopPage.screenshot({
    path: `${SCREENSHOTS_DIR}/quiz-genres.png`,
    fullPage: false
  });

  console.log('Capturing Quiz page mobile...');
  await mobilePage.goto(`${PROD_URL}/quiz`, { waitUntil: 'networkidle' });
  await mobilePage.waitForTimeout(1500);
  await mobilePage.screenshot({
    path: `${SCREENSHOTS_DIR}/quiz-genres-mobile.png`,
    fullPage: false
  });

  console.log('Capturing Login page...');
  await desktopPage.goto(`${PROD_URL}/login`, { waitUntil: 'networkidle' });
  await desktopPage.waitForTimeout(1000);
  await desktopPage.screenshot({
    path: `${SCREENSHOTS_DIR}/login.png`,
    fullPage: false
  });

  console.log('Capturing Recommendations page (may need auth)...');
  await desktopPage.goto(`${PROD_URL}/recommendations`, { waitUntil: 'networkidle' });
  await desktopPage.waitForTimeout(1500);
  await desktopPage.screenshot({
    path: `${SCREENSHOTS_DIR}/recommendations.png`,
    fullPage: false
  });

  console.log('Capturing My Data page (may need auth)...');
  await desktopPage.goto(`${PROD_URL}/my-data`, { waitUntil: 'networkidle' });
  await desktopPage.waitForTimeout(1500);
  await desktopPage.screenshot({
    path: `${SCREENSHOTS_DIR}/my-data.png`,
    fullPage: false
  });

  await browser.close();

  console.log(`\nScreenshots saved to ${SCREENSHOTS_DIR}/`);
  console.log('Files:');
  fs.readdirSync(SCREENSHOTS_DIR).forEach(file => {
    console.log(`  - ${file}`);
  });
}

captureScreenshots().catch(console.error);
