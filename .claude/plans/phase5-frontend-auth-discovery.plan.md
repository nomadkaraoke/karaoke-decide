# Plan: Phase 5 - Frontend Auth & Discovery

## Overview

Implement frontend authentication flow, user library pages, personalized recommendations, and quiz onboarding. This connects the Next.js frontend to the existing backend APIs (all ready) to complete the MLP user experience.

**Current State:** Single-page app with song search. No authentication, no user features.
**Target State:** Multi-page app with auth, My Songs, Recommendations, Quiz, and Services pages.

## Requirements

### Functional Requirements
1. **Authentication Flow**
   - Email-based magic link login (request + verify)
   - JWT token storage and automatic header injection
   - Protected routes redirect to login if unauthenticated
   - Logout clears token and redirects to home

2. **My Songs Page**
   - Display user's synced songs from Spotify/Last.fm/Quiz
   - Pagination (20 per page)
   - Show source badge, play count, times sung
   - Empty state prompts to connect services or take quiz

3. **Recommendations Page**
   - Display personalized song recommendations
   - Show recommendation score and reason
   - Filter by decade, minimum popularity
   - Empty state for users with no history

4. **Quiz Onboarding**
   - Step 1: Show 15 popular songs, user selects which they know
   - Step 2: Preferences (decade, energy level)
   - Step 3: Submit and show results
   - Redirect to recommendations on completion

5. **Services Page**
   - List connected services with sync status
   - Connect Spotify (OAuth redirect)
   - Connect Last.fm (username input)
   - Trigger sync, show progress
   - Disconnect services

### Non-Functional Requirements
- Maintain neon-noir design system consistency
- Mobile-responsive (already established)
- 60%+ E2E test coverage for new features
- No external state management library (React Context only)
- Static export compatibility (GitHub Pages)

## Technical Approach

### Architecture Decisions

1. **State Management:** React Context for auth state (simple, no extra deps)
2. **API Client:** Custom fetch wrapper with auth header injection
3. **Routing:** Next.js App Router with route groups for protected vs public
4. **Token Storage:** localStorage (simple, works with static export)
5. **Static Export:** Use client-side auth checks, not middleware

### Route Structure

```
src/app/
├── page.tsx                    # Home (public) - existing
├── login/page.tsx              # Login form (public)
├── auth/verify/page.tsx        # Token verification (public)
├── (protected)/                # Route group for auth-required pages
│   ├── layout.tsx              # Auth check wrapper
│   ├── my-songs/page.tsx       # User's song library
│   ├── recommendations/page.tsx # Personalized recommendations
│   ├── quiz/page.tsx           # Onboarding quiz
│   └── services/page.tsx       # Music service connections
│       └── spotify/
│           ├── success/page.tsx
│           └── error/page.tsx
└── layout.tsx                  # Root layout (existing)
```

### Component Structure

```
src/
├── components/
│   ├── ui/                     # Reusable UI components
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   ├── Card.tsx
│   │   ├── Badge.tsx
│   │   ├── LoadingSpinner.tsx
│   │   └── EmptyState.tsx
│   ├── SongCard.tsx            # Extract from page.tsx
│   ├── UserSongCard.tsx        # For My Songs page
│   ├── RecommendationCard.tsx  # For Recommendations page
│   ├── QuizSongCard.tsx        # For Quiz page
│   ├── Navigation.tsx          # Header with nav links
│   └── icons/                  # Extract icons from page.tsx
│       └── index.tsx
├── contexts/
│   └── AuthContext.tsx         # Auth state provider
├── lib/
│   ├── api.ts                  # API client with auth
│   └── constants.ts            # API URL, etc.
└── types/
    └── index.ts                # TypeScript interfaces
```

## Implementation Steps

### Phase 5.1: Foundation (Infrastructure)

1. **Extract shared components from page.tsx**
   - Create `src/components/icons/index.tsx` with MicrophoneIcon, SearchIcon, StarIcon
   - Create `src/components/SongCard.tsx` from existing SongCard
   - Create `src/components/ui/LoadingSpinner.tsx` from LoadingPulse
   - Update page.tsx to import from new locations

2. **Create API client utility**
   - `src/lib/constants.ts` - API_BASE_URL
   - `src/lib/api.ts` - fetch wrapper with:
     - Automatic Authorization header
     - 401 handling (clear token, redirect)
     - Type-safe generic methods

3. **Create TypeScript types**
   - `src/types/index.ts` - User, Song, UserSong, Recommendation, etc.

4. **Create Auth Context**
   - `src/contexts/AuthContext.tsx`
   - State: user, token, isLoading, isAuthenticated
   - Actions: login, logout, checkAuth
   - Provider wraps the app

### Phase 5.2: Authentication Flow

5. **Create Login page**
   - `src/app/login/page.tsx`
   - Email input form with validation
   - "Send Magic Link" button
   - Success message: "Check your email"
   - Error handling

6. **Create Verify page**
   - `src/app/auth/verify/page.tsx`
   - Extract token from URL query param
   - Auto-verify on mount
   - Loading state, error handling
   - Redirect to /my-songs on success

7. **Update Root Layout**
   - Wrap with AuthProvider
   - Add global navigation component

8. **Create Navigation component**
   - `src/components/Navigation.tsx`
   - Logo, nav links (Home, My Songs, Recommendations)
   - User menu (logged in) or Login button
   - Mobile hamburger menu

### Phase 5.3: Protected Routes & Layout

9. **Create protected route layout**
   - `src/app/(protected)/layout.tsx`
   - Check auth on mount
   - Redirect to /login if not authenticated
   - Show loading while checking

10. **Create UI components**
    - `src/components/ui/Button.tsx` - variants: primary, secondary, ghost
    - `src/components/ui/Input.tsx` - with label, error state
    - `src/components/ui/Badge.tsx` - for source indicators
    - `src/components/ui/EmptyState.tsx` - icon, title, description, action

### Phase 5.4: My Songs Page

11. **Create UserSongCard component**
    - `src/components/UserSongCard.tsx`
    - Title, artist, source badge
    - Play count, times sung
    - "Sing it!" button

12. **Create My Songs page**
    - `src/app/(protected)/my-songs/page.tsx`
    - Fetch user songs with pagination
    - Display UserSongCard list
    - Load more button
    - Empty state with links to /services and /quiz

### Phase 5.5: Recommendations Page

13. **Create RecommendationCard component**
    - `src/components/RecommendationCard.tsx`
    - Title, artist, score bar
    - Reason badge (e.g., "Similar artist")
    - Brand count stars
    - "Sing it!" button

14. **Create Recommendations page**
    - `src/app/(protected)/recommendations/page.tsx`
    - Fetch recommendations
    - Filter controls (decade dropdown, popularity slider)
    - Display RecommendationCard grid
    - Empty state for users with no history

### Phase 5.6: Quiz Onboarding

15. **Create QuizSongCard component**
    - `src/components/QuizSongCard.tsx`
    - Title, artist, decade tag
    - Selectable (checkbox/toggle)
    - Visual feedback when selected

16. **Create Quiz page**
    - `src/app/(protected)/quiz/page.tsx`
    - Multi-step wizard (3 steps)
    - Step 1: Song selection grid
    - Step 2: Preferences (decade, energy)
    - Step 3: Submit + results
    - Progress indicator
    - Submit to API, redirect to recommendations

### Phase 5.7: Music Services Page

17. **Create Services page**
    - `src/app/(protected)/services/page.tsx`
    - List connected services
    - "Connect Spotify" button (starts OAuth)
    - "Connect Last.fm" form (username input)
    - Sync status display
    - "Sync Now" button
    - Disconnect buttons

18. **Create Spotify callback pages**
    - `src/app/(protected)/services/spotify/success/page.tsx`
    - `src/app/(protected)/services/spotify/error/page.tsx`
    - Show result, redirect to services page

### Phase 5.8: Testing & Polish

19. **Write E2E tests**
    - `e2e/auth.spec.ts` - login flow, logout, protected routes
    - `e2e/my-songs.spec.ts` - load songs, pagination
    - `e2e/recommendations.spec.ts` - load recs, filters
    - `e2e/quiz.spec.ts` - complete quiz flow
    - `e2e/services.spec.ts` - connect/disconnect services

20. **Polish and accessibility**
    - Keyboard navigation
    - Focus management
    - ARIA labels
    - Loading states
    - Error boundaries

21. **Update home page**
    - Add "Get Started" CTA for logged-out users
    - Show "My Songs" shortcut for logged-in users

## Files to Create/Modify

### New Files (17 files)
```
src/lib/constants.ts
src/lib/api.ts
src/types/index.ts
src/contexts/AuthContext.tsx
src/components/icons/index.tsx
src/components/SongCard.tsx
src/components/UserSongCard.tsx
src/components/RecommendationCard.tsx
src/components/QuizSongCard.tsx
src/components/Navigation.tsx
src/components/ui/Button.tsx
src/components/ui/Input.tsx
src/components/ui/Badge.tsx
src/components/ui/EmptyState.tsx
src/components/ui/LoadingSpinner.tsx
```

### New Pages (10 pages)
```
src/app/login/page.tsx
src/app/auth/verify/page.tsx
src/app/(protected)/layout.tsx
src/app/(protected)/my-songs/page.tsx
src/app/(protected)/recommendations/page.tsx
src/app/(protected)/quiz/page.tsx
src/app/(protected)/services/page.tsx
src/app/(protected)/services/spotify/success/page.tsx
src/app/(protected)/services/spotify/error/page.tsx
```

### Modified Files
```
src/app/layout.tsx - Add AuthProvider wrapper
src/app/page.tsx - Extract components, add auth CTA
```

### New E2E Tests (5 files)
```
e2e/auth.spec.ts
e2e/my-songs.spec.ts
e2e/recommendations.spec.ts
e2e/quiz.spec.ts
e2e/services.spec.ts
```

## API Endpoints Used

| Endpoint | Method | Auth | Page |
|----------|--------|------|------|
| `/api/auth/magic-link` | POST | No | Login |
| `/api/auth/verify` | POST | No | Verify |
| `/api/auth/me` | GET | Yes | Auth check |
| `/api/auth/logout` | POST | Yes | Navigation |
| `/api/my/songs` | GET | Yes | My Songs |
| `/api/my/recommendations` | GET | Yes | Recommendations |
| `/api/quiz/songs` | GET | Yes | Quiz |
| `/api/quiz/submit` | POST | Yes | Quiz |
| `/api/quiz/status` | GET | Yes | Quiz |
| `/api/services` | GET | Yes | Services |
| `/api/services/spotify/connect` | POST | Yes | Services |
| `/api/services/lastfm/connect` | POST | Yes | Services |
| `/api/services/{type}` | DELETE | Yes | Services |
| `/api/services/sync` | POST | Yes | Services |

## Testing Strategy

### E2E Tests (Playwright)

1. **Auth Flow (`auth.spec.ts`)**
   - Login page renders correctly
   - Can submit email for magic link
   - Verify page handles token
   - Protected route redirects to login
   - Logout clears session

2. **My Songs (`my-songs.spec.ts`)**
   - Shows user's songs (mocked)
   - Pagination works
   - Empty state shown for new users

3. **Recommendations (`recommendations.spec.ts`)**
   - Shows recommendations (mocked)
   - Filters update results
   - Empty state shown

4. **Quiz (`quiz.spec.ts`)**
   - Shows quiz songs
   - Can select songs
   - Can set preferences
   - Submit completes quiz
   - Redirects to recommendations

5. **Services (`services.spec.ts`)**
   - Lists connected services
   - Spotify connect button works
   - Last.fm form works
   - Sync button triggers sync

### Test Data Strategy
- Use Playwright's request interception to mock API responses
- No real backend required for E2E tests
- Create fixtures for common responses

## Dependencies

No new npm dependencies required. Using:
- React 19 Context API for state
- Native fetch for HTTP
- Existing Tailwind CSS for styling
- Playwright for E2E testing

## Open Questions

1. **Email Testing:** How to test magic link flow end-to-end?
   - Option A: Use Mailslurp/similar service for real email testing
   - Option B: Mock the email endpoint and verify token directly
   - **Recommendation:** Start with Option B for CI, add Option A later

2. **Token Refresh:** Current JWT expires in 7 days with no refresh.
   - Acceptable for MLP
   - Consider adding refresh flow post-MLP

3. **Static Export Limitations:**
   - Can't use Next.js middleware for auth checks
   - Solution: Client-side auth check in protected layout
   - OAuth callback handled by backend, redirects to frontend

## Success Criteria

- [ ] User can log in via magic link
- [ ] User can view their synced songs
- [ ] User can see personalized recommendations
- [ ] User can complete the onboarding quiz
- [ ] User can connect/disconnect Spotify and Last.fm
- [ ] All pages are mobile-responsive
- [ ] E2E tests pass with 60%+ coverage
- [ ] No TypeScript errors
- [ ] Lighthouse accessibility score > 90

## Estimated Effort

| Phase | Effort |
|-------|--------|
| 5.1 Foundation | 2-3 hours |
| 5.2 Auth Flow | 3-4 hours |
| 5.3 Protected Routes | 2 hours |
| 5.4 My Songs | 2-3 hours |
| 5.5 Recommendations | 2-3 hours |
| 5.6 Quiz | 3-4 hours |
| 5.7 Services | 3-4 hours |
| 5.8 Testing & Polish | 4-5 hours |
| **Total** | **21-28 hours** |
