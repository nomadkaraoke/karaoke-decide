/**
 * TestMail.app API helper for E2E email testing
 *
 * TestMail.app uses a namespace/tag system instead of inbox creation:
 * - Namespace: Your unique identifier (from your account)
 * - Tag: Any string you choose to identify emails for a specific test
 * - Email format: {tag}@{namespace}.testmail.app
 *
 * Required environment variables:
 * - TESTMAIL_API_KEY: Your TestMail.app API key
 * - TESTMAIL_NAMESPACE: Your TestMail.app namespace
 */

const TESTMAIL_API_URL = "https://api.testmail.app/api/json";

export interface TestMailEmail {
  id: string;
  from: string;
  to: string;
  subject: string;
  text: string;
  html: string;
  timestamp: number;
  attachments: unknown[];
  downloadUrl: string;
}

interface TestMailResponse {
  result: "success" | "error";
  message?: string;
  count: number;
  limit: number;
  offset: number;
  emails: TestMailEmail[];
}

export interface TestMailConfig {
  apiKey: string;
  namespace: string;
}

/**
 * Generate a unique tag for test isolation
 */
export function generateTestTag(): string {
  return `test-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
}

/**
 * Get the email address for a given tag
 * Format: {namespace}.{tag}@inbox.testmail.app
 */
export function getEmailAddress(namespace: string, tag: string): string {
  return `${namespace}.${tag}@inbox.testmail.app`;
}

/**
 * Fetch emails from TestMail.app
 *
 * @param config - API key and namespace
 * @param tag - Tag to filter emails
 * @param options - Additional options
 * @returns Array of emails
 */
export async function fetchEmails(
  config: TestMailConfig,
  tag: string,
  options: {
    livequery?: boolean;
    timeout?: number;
  } = {}
): Promise<TestMailEmail[]> {
  const { livequery = false, timeout = 60000 } = options;

  const params = new URLSearchParams({
    apikey: config.apiKey,
    namespace: config.namespace,
    tag: tag,
    ...(livequery && { livequery: "true" }),
  });

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(`${TESTMAIL_API_URL}?${params}`, {
      signal: controller.signal,
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`TestMail API error (${response.status}): ${text}`);
    }

    const data: TestMailResponse = await response.json();

    if (data.result === "error") {
      throw new Error(`TestMail API error: ${data.message}`);
    }

    return data.emails;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Wait for an email to arrive with the given tag
 *
 * Uses TestMail.app's livequery feature which waits for an email to arrive
 * before returning.
 *
 * @param config - API key and namespace
 * @param tag - Tag to filter emails
 * @param timeout - Maximum time to wait in ms (default: 60000)
 * @returns The first email that matches
 */
export async function waitForEmail(
  config: TestMailConfig,
  tag: string,
  timeout = 60000
): Promise<TestMailEmail> {
  const emails = await fetchEmails(config, tag, { livequery: true, timeout });

  if (emails.length === 0) {
    throw new Error(`No email received for tag: ${tag}`);
  }

  return emails[0];
}

/**
 * Extract a URL matching a pattern from email content
 *
 * @param email - The email to search
 * @param pattern - Regex pattern to match URL
 * @returns The matched URL or null
 */
export function extractUrlFromEmail(
  email: TestMailEmail,
  pattern: RegExp
): string | null {
  // Try HTML first, then text
  const content = email.html || email.text || "";
  const match = content.match(pattern);
  return match ? match[0] : null;
}

/**
 * Create a TestMail client from environment variables
 */
export function createTestMailClient(): TestMailConfig | null {
  const apiKey = process.env.TESTMAIL_API_KEY;
  const namespace = process.env.TESTMAIL_NAMESPACE;

  if (!apiKey || !namespace) {
    return null;
  }

  return { apiKey, namespace };
}
