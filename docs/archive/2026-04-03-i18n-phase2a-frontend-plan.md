# Phase 2A: Karaoke Decide Frontend i18n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make decide.nomadkaraoke.com frontend fully multilingual (English, Spanish, German) with path-based locale routing, language switcher, and browser language detection — replicating the proven pattern from public-website Phase 1.

**Architecture:** next-intl with App Router `[locale]` dynamic segment. All hardcoded strings extracted into per-locale JSON message files (namespaced by page/feature). Static export generates `/en/`, `/es/`, `/de/` variants. Root `/` redirects based on browser language. Translation pipeline (from Phase 1) generates Spanish and German translations. API client sends `Accept-Language` header for future backend localization.

**Tech Stack:** Next.js 16.1.1, next-intl, TypeScript, Tailwind CSS, static export to GitHub Pages

**Design spec:** `docs/archive/2026-04-03-i18n-multilingual-design.md` (workspace root)

**Worktree:** `/Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual`
**Branch:** `feat/sess-20260403-1115-i18n-multilingual`

---

## File Structure

### New Files
- `frontend/src/i18n/routing.ts` — locale list, default locale, navigation helpers
- `frontend/src/i18n/request.ts` — message loading for next-intl
- `frontend/messages/en.json` — all English strings (single file, namespaced)
- `frontend/messages/es.json` — Spanish translations
- `frontend/messages/de.json` — German translations
- `frontend/src/app/[locale]/layout.tsx` — locale-aware layout (from existing layout.tsx)
- `frontend/src/app/[locale]/page.tsx` — homepage (moved)
- `frontend/src/app/[locale]/login/page.tsx` — login (moved)
- `frontend/src/app/[locale]/auth/verify/page.tsx` — verification (moved)
- `frontend/src/app/[locale]/quiz/page.tsx` — quiz (moved)
- `frontend/src/app/[locale]/recommendations/page.tsx` — recommendations (moved)
- `frontend/src/app/[locale]/my-data/page.tsx` — my data (moved)
- `frontend/src/app/[locale]/music-i-know/page.tsx` — music I know (moved)
- `frontend/src/app/[locale]/known-songs/page.tsx` — known songs (moved)
- `frontend/src/app/[locale]/my-songs/page.tsx` — my songs (moved)
- `frontend/src/app/[locale]/playlists/page.tsx` — playlists (moved)
- `frontend/src/app/[locale]/profile/page.tsx` — profile (moved)
- `frontend/src/app/[locale]/settings/page.tsx` — settings (moved)
- `frontend/src/app/[locale]/services/page.tsx` — services (moved)
- `frontend/src/app/[locale]/services/spotify/success/page.tsx` — spotify success (moved)
- `frontend/src/app/[locale]/services/spotify/error/page.tsx` — spotify error (moved)
- `frontend/src/app/[locale]/status/page.tsx` — status (moved)
- `frontend/src/app/[locale]/admin/layout.tsx` — admin layout (moved)
- `frontend/src/app/[locale]/admin/page.tsx` — admin dashboard (moved)
- `frontend/src/app/[locale]/admin/users/page.tsx` — admin users (moved)
- `frontend/src/app/[locale]/admin/users/detail/page.tsx` — admin user detail (moved)
- `frontend/src/app/[locale]/admin/sync-jobs/page.tsx` — admin sync jobs (moved)
- `frontend/src/app/[locale]/admin/sync-jobs/detail/page.tsx` — admin sync job detail (moved)
- `frontend/src/app/layout.tsx` — minimal root layout (passthrough with html/body)
- `frontend/src/app/page.tsx` — root redirect with browser language detection
- `frontend/src/components/LanguageSwitcher.tsx` — language dropdown

### Modified Files
- `frontend/package.json` — add next-intl dependency
- `frontend/next.config.ts` — add next-intl plugin
- `frontend/src/lib/api.ts` — add Accept-Language header to all API requests
- `frontend/src/components/Navigation.tsx` — add LanguageSwitcher, use useTranslations
- All page and component TSX files — replace hardcoded strings with `t()` calls

---

## Task 1: Install next-intl and Create Routing Infrastructure

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/next.config.ts`
- Create: `frontend/src/i18n/routing.ts`
- Create: `frontend/src/i18n/request.ts`
- Create: `frontend/messages/en.json` (empty placeholder)

- [ ] **Step 1: Install next-intl**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual/frontend
npm install next-intl
```

- [ ] **Step 2: Create routing config**

Create `frontend/src/i18n/routing.ts`:

```typescript
import { defineRouting } from 'next-intl/routing';
import { createNavigation } from 'next-intl/navigation';

export const routing = defineRouting({
  locales: ['en', 'es', 'de'],
  defaultLocale: 'en'
});

export const { Link, redirect, usePathname, useRouter } =
  createNavigation(routing);
```

- [ ] **Step 3: Create request config**

Create `frontend/src/i18n/request.ts`:

```typescript
import { getRequestConfig } from 'next-intl/server';
import { hasLocale } from 'next-intl';
import { routing } from './routing';

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested)
    ? requested
    : routing.defaultLocale;

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default
  };
});
```

- [ ] **Step 4: Update next.config.ts**

```typescript
import type { NextConfig } from "next";
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

const nextConfig: NextConfig = {
  output: "export",
  images: {
    unoptimized: true,
  },
  async rewrites() {
    if (process.env.PROXY_TO_PROD) {
      return [
        {
          source: "/api/:path*",
          destination: "https://decide.nomadkaraoke.com/api/:path*",
        },
      ];
    }
    return [];
  },
};

export default withNextIntl(nextConfig);
```

- [ ] **Step 5: Create placeholder messages file**

Create `frontend/messages/en.json`:
```json
{}
```

- [ ] **Step 6: Verify type-check passes**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual/frontend
npm run type-check
```

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/i18n/ frontend/next.config.ts frontend/messages/
git commit -m "feat(i18n): install next-intl and create routing infrastructure"
```

---

## Task 2: Create English Message File

Extract ALL hardcoded strings from the entire frontend into `messages/en.json`. This is the source of truth for all translations.

**Files:**
- Modify: `frontend/messages/en.json`

- [ ] **Step 1: Read every page and component file**

Read ALL TSX files under `frontend/src/` to identify every user-facing hardcoded string. Group them into namespaces.

- [ ] **Step 2: Write the complete en.json**

The file should use a flat namespace structure organized by feature area. Structure:

```json
{
  "metadata": { ... },
  "nav": { ... },
  "home": { ... },
  "auth": { ... },
  "quiz": { ... },
  "recommendations": { ... },
  "myData": { ... },
  "musicIKnow": { ... },
  "knownSongs": { ... },
  "mySongs": { ... },
  "playlists": { ... },
  "profile": { ... },
  "settings": { ... },
  "services": { ... },
  "admin": { ... },
  "status": { ... },
  "components": { ... },
  "common": { ... },
  "errors": { ... },
  "languageSwitcher": { ... }
}
```

Each namespace covers one page or feature area. The `common` namespace is for shared strings (buttons like "Back", "Continue", "Cancel", "Try again", "Loading..."). The `errors` namespace is for all error messages. The `components` namespace is for shared component strings (SongCard, RecommendationCard, etc.).

CRITICAL: Read every TSX file before writing. Don't miss ANY user-facing string. Include:
- Page headings and subheadings
- Button labels
- Form labels and placeholders
- Error messages and empty states
- Tooltips and aria-labels
- Modal titles and descriptions
- Badge text
- Navigation labels
- Stats labels
- Quiz options (genres, decades, energy levels, etc.)
- Genre names with example artists
- Success/confirmation messages

Use `{variable}` placeholders for dynamic content (e.g., `"{count} artist(s)"` → `"{count} artist(s)"`).

- [ ] **Step 3: Validate JSON**

```bash
python3 -c "import json; json.load(open('frontend/messages/en.json')); print('Valid JSON')"
```

- [ ] **Step 4: Commit**

```bash
git add frontend/messages/en.json
git commit -m "feat(i18n): extract all English strings into messages/en.json"
```

---

## Task 3: Restructure App Directory for Locale Routing

Move ALL pages under `[locale]/` dynamic segment. Create root layout and browser-detecting root page.

**Files:**
- Replace: `frontend/src/app/layout.tsx` (minimal root with html/body)
- Create: `frontend/src/app/[locale]/layout.tsx` (locale-aware, from existing layout)
- Create: `frontend/src/app/page.tsx` (root redirect)
- Move: all pages from `frontend/src/app/*/` to `frontend/src/app/[locale]/*/`

- [ ] **Step 1: Read existing layout.tsx**

Read `frontend/src/app/layout.tsx` fully — you need all its content for the locale layout.

- [ ] **Step 2: Create [locale] directory structure**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual/frontend
mkdir -p "src/app/[locale]/login" \
  "src/app/[locale]/auth/verify" \
  "src/app/[locale]/quiz" \
  "src/app/[locale]/recommendations" \
  "src/app/[locale]/my-data" \
  "src/app/[locale]/music-i-know" \
  "src/app/[locale]/known-songs" \
  "src/app/[locale]/my-songs" \
  "src/app/[locale]/playlists" \
  "src/app/[locale]/profile" \
  "src/app/[locale]/settings" \
  "src/app/[locale]/services/spotify/success" \
  "src/app/[locale]/services/spotify/error" \
  "src/app/[locale]/status" \
  "src/app/[locale]/admin/users/detail" \
  "src/app/[locale]/admin/sync-jobs/detail"
```

- [ ] **Step 3: Create minimal root layout**

Replace `frontend/src/app/layout.tsx` with:

```tsx
import './globals.css';

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="icon" href="/favicon.ico" sizes="any" />
        <link rel="icon" href="/favicon-16x16.png" sizes="16x16" type="image/png" />
        <link rel="icon" href="/favicon-32x32.png" sizes="32x32" type="image/png" />
        <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
      </head>
      <body className="font-sans antialiased" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
        {children}
      </body>
    </html>
  );
}
```

- [ ] **Step 4: Create locale-aware layout**

Create `frontend/src/app/[locale]/layout.tsx` — this is the existing layout content, modified for i18n:

```tsx
import type { Metadata } from "next";
import { AuthProvider } from "@/contexts/AuthContext";
import { Navigation } from "@/components/Navigation";
import { ThemeProvider } from "@/components/theme-provider";
import { GoogleAnalytics } from "@/components/GoogleAnalytics";
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { hasLocale } from 'next-intl';
import { notFound } from 'next/navigation';
import { routing } from '@/i18n/routing';

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

const ogLocaleMap: Record<string, string> = {
  en: 'en_US',
  es: 'es_ES',
  de: 'de_DE',
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  const t = await getTranslations({ locale, namespace: 'metadata' });

  return {
    title: t('title'),
    description: t('description'),
    keywords: t('keywords').split(', '),
    authors: [{ name: 'Nomad Karaoke' }],
    openGraph: {
      title: t('ogTitle'),
      description: t('ogDescription'),
      url: 'https://decide.nomadkaraoke.com',
      siteName: t('siteName'),
      type: 'website',
      locale: ogLocaleMap[locale] ?? 'en_US',
    },
    manifest: '/manifest.json',
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }

  setRequestLocale(locale);
  const messages = await getMessages();

  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem
      disableTransitionOnChange
    >
      <NextIntlClientProvider messages={messages}>
        <script
          dangerouslySetInnerHTML={{
            __html: `document.documentElement.lang="${locale}";`,
          }}
        />
        <GoogleAnalytics />
        <AuthProvider>
          <Navigation />
          <div className="h-[72px]" />
          {children}
        </AuthProvider>
      </NextIntlClientProvider>
    </ThemeProvider>
  );
}
```

Note: Check the existing layout for exact providers, class names, ordering. Match exactly, just adding the i18n layer.

- [ ] **Step 5: Move all pages to [locale]/**

Move every page file. For each page, add `setRequestLocale(locale)` call if it's a server component, or leave as-is if client component (most pages here are `'use client'`).

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual/frontend/src/app

# Move each page (repeat for all)
mv page.tsx "[locale]/page.tsx"
mv login/page.tsx "[locale]/login/page.tsx" && rmdir login
mv auth/verify/page.tsx "[locale]/auth/verify/page.tsx" && rmdir -p auth/verify
mv quiz/page.tsx "[locale]/quiz/page.tsx" && rmdir quiz
mv recommendations/page.tsx "[locale]/recommendations/page.tsx" && rmdir recommendations
mv my-data/page.tsx "[locale]/my-data/page.tsx" && rmdir my-data
mv music-i-know/page.tsx "[locale]/music-i-know/page.tsx" && rmdir music-i-know
mv known-songs/page.tsx "[locale]/known-songs/page.tsx" && rmdir known-songs
mv my-songs/page.tsx "[locale]/my-songs/page.tsx" && rmdir my-songs
mv playlists/page.tsx "[locale]/playlists/page.tsx" && rmdir playlists
mv profile/page.tsx "[locale]/profile/page.tsx" && rmdir profile
mv settings/page.tsx "[locale]/settings/page.tsx" && rmdir settings
mv services/page.tsx "[locale]/services/page.tsx"
mv services/spotify/success/page.tsx "[locale]/services/spotify/success/page.tsx"
mv services/spotify/error/page.tsx "[locale]/services/spotify/error/page.tsx"
rm -r services
mv status/page.tsx "[locale]/status/page.tsx" && rmdir status
mv admin/layout.tsx "[locale]/admin/layout.tsx"
mv admin/page.tsx "[locale]/admin/page.tsx"
mv admin/users/page.tsx "[locale]/admin/users/page.tsx"
mv admin/users/detail/page.tsx "[locale]/admin/users/detail/page.tsx"
mv admin/sync-jobs/page.tsx "[locale]/admin/sync-jobs/page.tsx"
mv admin/sync-jobs/detail/page.tsx "[locale]/admin/sync-jobs/detail/page.tsx"
rm -r admin
```

- [ ] **Step 6: Create root redirect page**

Create `frontend/src/app/page.tsx`:

```tsx
'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { routing } from '@/i18n/routing';

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    try {
      const saved = localStorage.getItem('locale');
      if (saved && routing.locales.includes(saved as typeof routing.locales[number])) {
        router.replace(`/${saved}/`);
        return;
      }
    } catch {
      // localStorage unavailable
    }

    const browserLangs = navigator.languages || [navigator.language];
    for (const lang of browserLangs) {
      const prefix = lang.split('-')[0].toLowerCase();
      if (routing.locales.includes(prefix as typeof routing.locales[number])) {
        router.replace(`/${prefix}/`);
        return;
      }
    }

    router.replace(`/${routing.defaultLocale}/`);
  }, [router]);

  return null;
}
```

- [ ] **Step 7: Verify type-check passes**

```bash
npm run type-check
```

Fix any import path issues caused by the move.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(i18n): restructure app directory for [locale] routing"
```

---

## Task 4: Convert Core Components to Use Translations

Convert the shared components that appear on every page: Navigation, LanguageSwitcher, and other shared UI.

**Files:**
- Create: `frontend/src/components/LanguageSwitcher.tsx`
- Modify: `frontend/src/components/Navigation.tsx`
- Modify: `frontend/src/components/SongCard.tsx`
- Modify: `frontend/src/components/RecommendationCard.tsx`
- Modify: `frontend/src/components/ProtectedPage.tsx`
- Modify: `frontend/src/components/EnjoySingingModal.tsx`
- Modify: `frontend/src/components/PostQuizEmailModal.tsx`
- Modify: `frontend/src/components/StickyFinishBar.tsx`
- Modify: `frontend/src/components/UpgradePrompt.tsx`
- Modify: `frontend/src/components/ThemeToggle.tsx`

- [ ] **Step 1: Create LanguageSwitcher**

Create `frontend/src/components/LanguageSwitcher.tsx`:

```tsx
'use client';

import { useLocale, useTranslations } from 'next-intl';
import { usePathname, useRouter, routing } from '@/i18n/routing';

const localeNames: Record<string, string> = {
  en: 'English',
  es: 'Español',
  de: 'Deutsch',
};

export default function LanguageSwitcher() {
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations('languageSwitcher');

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const newLocale = e.target.value;
    try {
      localStorage.setItem('locale', newLocale);
    } catch {}
    router.replace(pathname, { locale: newLocale });
  }

  return (
    <select
      value={locale}
      onChange={handleChange}
      aria-label={t('label')}
      className="bg-transparent border border-border rounded-md px-2 py-1 text-sm cursor-pointer"
    >
      {routing.locales.map((loc) => (
        <option key={loc} value={loc}>
          {localeNames[loc]}
        </option>
      ))}
    </select>
  );
}
```

- [ ] **Step 2: Convert Navigation.tsx**

Add `useTranslations('nav')` and replace all hardcoded strings. Add LanguageSwitcher import and place it in both desktop and mobile nav sections.

Key strings to replace:
- Nav link labels: "Recommendations", "Music I Know", "Playlists", "Settings", "Admin"
- Auth labels: "Guest", "Create Account", "Sign In", "Profile settings", "Log out", "Clear session"
- Mobile menu: "Close menu", "Open menu", "Guest Session", etc.

- [ ] **Step 3: Convert all remaining shared components**

For each component, add `import { useTranslations } from 'next-intl'` and replace hardcoded strings with `t()` calls using the appropriate namespace:
- SongCard → `components.songCard`
- RecommendationCard → `components.recommendationCard`
- ProtectedPage → `common`
- EnjoySingingModal → `components.enjoySinging`
- PostQuizEmailModal → `components.postQuizEmail`
- StickyFinishBar → `components.stickyFinish`
- UpgradePrompt → `components.upgrade`
- ThemeToggle → `common`

- [ ] **Step 4: Convert MyData sub-components**

- MyData/ConnectedServicesSection → `myData.services`
- MyData/PreferencesSection → `myData.preferences`
- MyData/YourArtistsSection → `myData.artists`
- MyData/YourSongsSection → `myData.songs`

- [ ] **Step 5: Verify type-check**

```bash
npm run type-check
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(i18n): convert shared components to use useTranslations"
```

---

## Task 5: Convert Landing, Auth, and Quiz Pages

The three highest-traffic page groups.

**Files:**
- Modify: `frontend/src/app/[locale]/page.tsx` (landing page — largest, ~50 strings)
- Modify: `frontend/src/app/[locale]/login/page.tsx`
- Modify: `frontend/src/app/[locale]/auth/verify/page.tsx`
- Modify: `frontend/src/app/[locale]/quiz/page.tsx` (most complex — quiz steps, genres, decades)

- [ ] **Step 1: Convert landing page**

Add `useTranslations('home')`. Replace all hardcoded strings — headings, subheadings, pain points, path descriptions, feature cards, CTAs, footer links.

- [ ] **Step 2: Convert login page**

Add `useTranslations('auth')`. Replace form labels, button text, success/error messages, helper text.

- [ ] **Step 3: Convert auth verify page**

Add `useTranslations('auth')`. Replace all verification states — loading, success, various error messages, troubleshooting tips.

- [ ] **Step 4: Convert quiz page**

Add `useTranslations('quiz')`. This is the most complex page — 6 steps with:
- Step titles and descriptions
- Genre names (Pop, Rock, Hip-Hop, etc.) — these are used as labels AND as data sent to the API, so use translation keys for display but keep English values for API calls
- Example artists per genre (keep as-is — artist names don't translate)
- Decade labels
- Energy/vocal/crowd preference options — same concern: translate display, keep English for API
- Validation messages

IMPORTANT: Genre names, decade labels, and preference options serve dual purpose (display + API data). The `t()` call handles display; keep the original English values for any data sent to the backend.

- [ ] **Step 5: Verify type-check and build**

```bash
npm run type-check
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(i18n): convert landing, auth, and quiz pages"
```

---

## Task 6: Convert Recommendations, Music, and Data Pages

**Files:**
- Modify: `frontend/src/app/[locale]/recommendations/page.tsx`
- Modify: `frontend/src/app/[locale]/my-data/page.tsx`
- Modify: `frontend/src/app/[locale]/music-i-know/page.tsx`
- Modify: `frontend/src/app/[locale]/known-songs/page.tsx`
- Modify: `frontend/src/app/[locale]/my-songs/page.tsx`
- Modify: `frontend/src/app/[locale]/playlists/page.tsx`

- [ ] **Step 1: Convert recommendations page**

Add `useTranslations('recommendations')`. Replace filter labels, section headings, empty states, dropdown options (popularity, duration, etc.).

- [ ] **Step 2: Convert my-data page**

Add `useTranslations('myData')`. Replace headings, stat labels, section titles, footer messages.

- [ ] **Step 3: Convert music-i-know page**

Add `useTranslations('musicIKnow')`. Replace tab labels, search placeholders, empty states, count messages.

- [ ] **Step 4: Convert known-songs, my-songs, playlists pages**

Add appropriate `useTranslations` calls for each. Replace all hardcoded strings.

- [ ] **Step 5: Verify type-check**

```bash
npm run type-check
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(i18n): convert recommendations, music, and data pages"
```

---

## Task 7: Convert Settings, Profile, Services, and Status Pages

**Files:**
- Modify: `frontend/src/app/[locale]/profile/page.tsx`
- Modify: `frontend/src/app/[locale]/settings/page.tsx`
- Modify: `frontend/src/app/[locale]/services/page.tsx`
- Modify: `frontend/src/app/[locale]/services/spotify/success/page.tsx`
- Modify: `frontend/src/app/[locale]/services/spotify/error/page.tsx`
- Modify: `frontend/src/app/[locale]/status/page.tsx`

- [ ] **Step 1: Convert profile and settings pages**

Add `useTranslations('settings')` / `useTranslations('profile')`. Replace form labels, section headings, danger zone text, confirmation dialogs.

- [ ] **Step 2: Convert services and Spotify callback pages**

Add `useTranslations('services')`. Replace connection status messages, success/error states.

- [ ] **Step 3: Convert status page**

Add `useTranslations('status')`. Replace any status indicators.

- [ ] **Step 4: Verify type-check**

```bash
npm run type-check
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(i18n): convert settings, profile, services, and status pages"
```

---

## Task 8: Convert Admin Pages

Lower priority — admin-only pages, but still need localization for completeness.

**Files:**
- Modify: `frontend/src/app/[locale]/admin/layout.tsx`
- Modify: `frontend/src/app/[locale]/admin/page.tsx`
- Modify: `frontend/src/app/[locale]/admin/users/page.tsx`
- Modify: `frontend/src/app/[locale]/admin/users/detail/page.tsx`
- Modify: `frontend/src/app/[locale]/admin/sync-jobs/page.tsx`
- Modify: `frontend/src/app/[locale]/admin/sync-jobs/detail/page.tsx`
- Modify: `frontend/src/components/AdminPage.tsx`

- [ ] **Step 1: Convert admin dashboard and layout**

Add `useTranslations('admin')`. Replace stat labels, section headings, link text.

- [ ] **Step 2: Convert admin users and detail pages**

Replace table headers, action buttons, status labels.

- [ ] **Step 3: Convert admin sync-jobs pages**

Replace job status labels, timestamps, detail fields.

- [ ] **Step 4: Verify type-check**

```bash
npm run type-check
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(i18n): convert admin pages"
```

---

## Task 9: Update Internal Links and API Client

Make all internal links locale-aware and add Accept-Language header to API requests.

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: Any components using `Link` from `next/link` or `useRouter` from `next/navigation`

- [ ] **Step 1: Update API client to send Accept-Language header**

In `frontend/src/lib/api.ts`, modify the `apiRequest` function to include the current locale:

```typescript
// Add at top of file:
function getCurrentLocale(): string {
  if (typeof window === 'undefined') return 'en';
  // Extract locale from URL path
  const match = window.location.pathname.match(/^\/(en|es|de)\//);
  return match ? match[1] : 'en';
}

// In apiRequest function, add to headers:
const headers: Record<string, string> = {
  "Content-Type": "application/json",
  "Accept-Language": getCurrentLocale(),
  ...(options.headers as Record<string, string>),
};
```

- [ ] **Step 2: Audit and update internal links**

Search all components for `import Link from 'next/link'` and `import { useRouter } from 'next/navigation'`. Replace with:
- `import { Link } from '@/i18n/routing'` for Link components
- `import { useRouter } from '@/i18n/routing'` for programmatic navigation

Also search for `usePathname` from `next/navigation` and replace with the one from `@/i18n/routing`.

EXCEPTION: The root `app/page.tsx` (redirect page) must keep using `next/navigation` since it's outside the `[locale]` segment.

- [ ] **Step 3: Verify type-check**

```bash
npm run type-check
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(i18n): make links locale-aware and add Accept-Language to API client"
```

---

## Task 10: Generate Translations and Verify Build

Copy the translation pipeline from Phase 1 and generate Spanish and German translations.

**Files:**
- Create: `frontend/messages/es.json`
- Create: `frontend/messages/de.json`

- [ ] **Step 1: Copy translation pipeline from public-website**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual
mkdir -p scripts
cp /Users/andrew/Projects/nomadkaraoke/public-website-i18n-multilingual/scripts/translate.py scripts/
cp /Users/andrew/Projects/nomadkaraoke/public-website-i18n-multilingual/scripts/glossary.json scripts/
cp /Users/andrew/Projects/nomadkaraoke/public-website-i18n-multilingual/scripts/requirements.txt scripts/
```

- [ ] **Step 2: Run translation pipeline**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual
python scripts/translate.py --messages-dir ./frontend/messages --target es de
```

- [ ] **Step 3: Spot-check translations**

Verify:
- All JSON keys match en.json
- Brand names preserved
- Placeholders preserved
- Natural-sounding translations

- [ ] **Step 4: Run full production build**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual/frontend
npm run build
```

Expected: Build succeeds generating all locale variants.

- [ ] **Step 5: Start dev server and verify**

```bash
npm run dev
```

Test:
- `http://localhost:3000/en/` — English homepage
- `http://localhost:3000/es/` — Spanish
- `http://localhost:3000/de/` — German
- Language switcher works
- Root `/` redirects correctly
- Navigate through quiz, recommendations, settings — all localized

- [ ] **Step 6: Run lint and type-check**

```bash
npm run type-check
npm run lint
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(i18n): add Spanish and German translations, verify build"
```

---

## Task 11: Final Cleanup and Documentation

- [ ] **Step 1: Verify no hardcoded English strings remain**

Search components for obvious remaining hardcoded strings:

```bash
grep -rn '"[A-Z][a-z]' frontend/src/ --include="*.tsx" | grep -v "import\|from\|className\|href\|src\|key\|type\|name\|value\|aria\|role\|node_modules"
```

Review results — fix any remaining hardcoded user-facing strings.

- [ ] **Step 2: Update CLAUDE.md**

Add i18n section to CLAUDE.md following the pattern from public-website Phase 1.

- [ ] **Step 3: Clean build and type-check**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-decide-i18n-multilingual/frontend
rm -rf .next out
npm run build
npm run type-check
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(i18n): final cleanup and documentation"
```

---

## Summary

After completing all tasks, the karaoke-decide frontend will:
- Serve fully localized pages at `/en/`, `/es/`, `/de/`
- Auto-detect browser language and redirect from `/`
- Allow users to switch language via header dropdown
- Persist language preference in localStorage
- Send `Accept-Language` header with all API requests (ready for Phase 2B backend)
- Have Spanish and German translations with full key coverage

Phase 2B (backend i18n) will add: FastAPI locale middleware, backend translation files, email template localization, and user locale preference in Firestore.
