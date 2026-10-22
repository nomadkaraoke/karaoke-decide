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

// Sign-in goes straight to the Nomad Karaoke (gen) API, whose CORS allows any
// origin. decide's backend accepts the resulting gen session token as bearer.
export const GEN_API_URL =
  process.env.NEXT_PUBLIC_GEN_API_URL || "https://api.nomadkaraoke.com";

// Google sign-in button is only rendered when this is set.
export const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

export const GUEST_TOKEN_KEY = "karaoke_decide_guest_token";

// gen emails a code of this many digits
export const LOGIN_CODE_LENGTH = 6;
