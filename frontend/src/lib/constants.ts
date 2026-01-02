/**
 * Application constants
 */

// In production, use relative URLs (empty string) so requests go through
// Cloudflare Worker proxy at /api/*. For local development, set
// NEXT_PUBLIC_API_URL=http://localhost:8000
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

export const AUTH_TOKEN_KEY = "karaoke_decide_token";

export const PAGINATION = {
  DEFAULT_PAGE_SIZE: 20,
  MAX_PAGE_SIZE: 100,
} as const;
