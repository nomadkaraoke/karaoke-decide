/**
 * Application constants
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://karaoke-decide-718638054799.us-central1.run.app";

export const AUTH_TOKEN_KEY = "karaoke_decide_token";

export const PAGINATION = {
  DEFAULT_PAGE_SIZE: 20,
  MAX_PAGE_SIZE: 100,
} as const;
