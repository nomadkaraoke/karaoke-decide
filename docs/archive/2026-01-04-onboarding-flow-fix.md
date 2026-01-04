# Onboarding Flow Fix - 2026-01-04

## Summary

Fixed a critical bug where new users clicking "Get Started" on the landing page were sent directly to `/recommendations` instead of the quiz, bypassing the entire onboarding flow.

## Problem

The landing page promised "Quick Quiz" as step 1 in "How It Works", but users never saw it because:

1. User clicks "Get Started"
2. `startGuestSession()` creates guest session and sets `isAuthenticated = true`
3. `router.push("/quiz")` is called
4. BUT a `useEffect` watching `isAuthenticated` immediately triggers `router.push("/recommendations")`
5. The recommendations redirect **wins the race**, bypassing the quiz

Users arrived at recommendations with empty personalized sections ("From Artists You Know (0)") and no guidance.

## Solution

### 1. Quiz Completion Tracking (AuthContext)

Added state and API integration to track whether user has completed the quiz:

```typescript
// New state in AuthContext
hasCompletedQuiz: boolean;
quizStatusLoading: boolean;
refreshQuizStatus: () => Promise<void>;
```

Quiz status is fetched automatically when user authenticates.

### 2. Smart Redirect Logic (Landing Page)

Changed redirect to check quiz completion:

```typescript
// Before: Always redirect authenticated users to recommendations
if (isAuthenticated) router.push("/recommendations");

// After: Check quiz status
if (hasCompletedQuiz) {
  router.push("/recommendations");
} else {
  router.push("/quiz");
}
```

### 3. Quiz Prompt Banner (Recommendations Page)

For users who somehow skip the quiz (direct URL, etc.), added a prominent banner:

```
+--------------------------------------------------+
| Get personalized recommendations                  |
| Take a quick 30-second quiz to tell us your      |
| music taste                          [Take Quiz] |
+--------------------------------------------------+
```

### 4. Context-Aware Empty States

Empty section messages now adapt to user's state:

| Section | Quiz Not Completed | Filters Active | Default |
|---------|-------------------|----------------|---------|
| Artists You Know | "Take the quiz above" | "No songs match filters" | "Connect Spotify/Last.fm" |
| Create Your Own | "Take the quiz to unlock" | "No songs match filters" | "Connect music services" |

## Files Changed

- `frontend/src/contexts/AuthContext.tsx` - Quiz completion tracking
- `frontend/src/app/page.tsx` - Smart redirect logic
- `frontend/src/app/quiz/page.tsx` - Redirect if quiz completed, refresh status after submit
- `frontend/src/app/recommendations/page.tsx` - Quiz banner, context-aware empty states
- `frontend/e2e/onboarding-flow.spec.ts` - New E2E tests (8 tests)

## User Flows

### New User (After Fix)
```
Landing → "Get Started" → Quiz (3 steps) → Recommendations
```

### Returning User (Quiz Completed)
```
Landing → Auto-redirect → Recommendations
```

### Returning User (Quiz Not Completed)
```
Landing → Auto-redirect → Quiz
```

## Testing

New E2E test file `onboarding-flow.spec.ts` covers:
- New user goes to quiz on "Get Started"
- Quiz page shows genres
- Quiz progress through all steps
- Quiz banner appears when quiz not completed
- Quiz banner click navigates to quiz
- Returning user (quiz completed) goes to recommendations
- No quiz banner after quiz completed

## Lessons Learned

See entry in `LESSONS-LEARNED.md`: "useEffect Race Conditions with State Changes"
