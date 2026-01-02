# Guest User Onboarding Flow

**Date:** 2026-01-02
**PR:** #31
**Status:** Deployed to production

## Summary

Implemented a guest user onboarding flow that allows users to try the app (quiz, recommendations) without requiring email verification. Email verification is only required when connecting music services (Spotify/Last.fm).

## Problem

Previously, users had to provide and verify their email before they could explore the app. This created friction in the onboarding process - users couldn't see the value of the app until after they committed to signing up.

## Solution

### Backend Changes

1. **Guest Session Endpoint** (`POST /api/auth/guest`)
   - Creates anonymous guest user with `guest_xxx` ID
   - Returns JWT with `is_guest: true` and 30-day expiration
   - Guest users stored in Firestore with `is_guest: true` flag

2. **Upgrade Endpoint** (`POST /api/auth/upgrade`)
   - Allows guest users to upgrade to verified accounts
   - Sends magic link email
   - On verification, migrates all guest data (quiz results, etc.) to verified account
   - Handles merging if email already has an account

3. **VerifiedUser Dependency**
   - New dependency for routes requiring verified users
   - Services routes (Spotify, Last.fm) now require verified users
   - Returns 403 with clear message for guest users

4. **User Model Update**
   - `email` field now optional (None for guests)
   - Added `is_guest: bool` field

### Frontend Changes

1. **Home Page Welcome Banner**
   - Prominent "Get Started" button for unauthenticated users
   - Creates guest session and navigates to quiz

2. **Quiz Page**
   - Automatically creates guest session if not authenticated
   - No longer requires email to complete quiz

3. **Navigation Updates**
   - Shows "Guest" badge for guest users
   - Shows "Create Account" button instead of profile menu
   - Services link hidden for guest users

4. **UpgradePrompt Component**
   - Reusable component for prompting guests to upgrade
   - Shows benefits of creating an account
   - Email form to request verification link

5. **AuthContext Updates**
   - Added `isGuest`, `isVerified` boolean flags
   - Added `startGuestSession()` method
   - Added `requestUpgrade(email)` method

## API Changes

New endpoints:
- `POST /api/auth/guest` - Create guest session (no auth required)
- `POST /api/auth/upgrade` - Request guest upgrade (requires guest auth)

Modified responses:
- `GET /api/auth/me` now includes `is_guest: boolean` field

Services endpoints now return 403 for guest users with message:
```json
{"detail": "Email verification required. Please verify your email to use this feature."}
```

## Testing

- All existing backend tests pass (278 tests)
- All unit tests pass (135 tests)
- E2E tests added for guest flow:
  - Welcome banner visibility
  - Get Started button creates guest session
  - Guest badge in navigation
  - Create Account button for guests
  - Services page upgrade prompt

## User Flow

1. User visits https://decide.nomadkaraoke.com
2. Sees welcome banner with "Get Started" button
3. Clicks "Get Started" → guest session created automatically
4. Completes quiz as guest
5. Views personalized recommendations
6. When trying to connect Spotify/Last.fm, sees upgrade prompt
7. Enters email, receives magic link
8. Verifies email → guest data migrated to verified account
