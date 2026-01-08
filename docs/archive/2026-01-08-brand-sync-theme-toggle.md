# Brand Sync & Theme Toggle (2026-01-08)

## Summary

Synchronized Decide UI with Nomad Karaoke Generator branding, added light/dark theme toggle, and overhauled the homepage with problem-focused content.

## What Changed

### Brand Color Update

Updated from neon colors to cohesive brand palette:

| Element | Before | After |
|---------|--------|-------|
| Primary Pink | `#ff2d92` (neon) | `#ff7acc` (brand pink) |
| Secondary | `#00f5ff` (cyan) | `#3b82f6` (brand blue) |
| Accent | `#b347ff` | `#8b5cf6` (brand purple) |
| Yellow | `#ffeb3b` | `#ffdf6b` (brand gold) |
| Background | `#0a0a0f` | `#0f0f0f` |

### Light/Dark Theme Support

- Added theme toggle in header (sun/moon icon)
- System preference detection on first visit
- CSS variables for all colors in both themes
- Fixed 40+ files with hardcoded colors

### Homepage Overhaul

New problem-focused structure:
1. **Hero** - Simple headline matching Generator style: "Easily Choose Karaoke Songs to Sing"
2. **Problem Section** - Four pain points from VISION.md
3. **Two Paths** - Quiz (2 minutes) vs Connect Services
4. **Generator Integration** - "Not in the catalog? Make it in 30 minutes"
5. **Screenshots** - Quiz, Recommendations, My Data pages
6. **Features** - Six key capabilities
7. **Final CTA** - Get started button

### Documentation

Added three new docs from Generator:
- `BRAND-STYLE-GUIDE.md` - Colors, typography, themes
- `MOBILE-UX-BEST-PRACTICES.md` - Mobile UX patterns
- `PRODUCT-COMMUNICATION-GUIDE.md` - Voice and messaging

## Files Modified

### New Files
- `docs/BRAND-STYLE-GUIDE.md`
- `docs/MOBILE-UX-BEST-PRACTICES.md`
- `docs/PRODUCT-COMMUNICATION-GUIDE.md`
- `frontend/src/lib/theme.ts`
- `frontend/src/components/theme-provider.tsx`
- `frontend/src/components/ThemeToggle.tsx`
- `frontend/public/screenshots/quiz.avif`
- `frontend/public/screenshots/recommendations.avif`
- `frontend/public/screenshots/my-data.avif`

### Major Updates
- `frontend/src/app/globals.css` - Theme CSS variables
- `frontend/src/app/page.tsx` - Complete homepage rewrite
- `frontend/src/app/layout.tsx` - ThemeProvider integration
- `frontend/src/components/Navigation.tsx` - Theme toggle

### Light Mode Fixes (40+ files)
Replaced hardcoded colors with CSS variables:
- `text-white` → `text-[var(--text)]`
- `text-white/60` → `text-[var(--text-muted)]`
- `bg-[rgba(20,20,30,0.9)]` → `bg-[var(--card)]`
- `#ff2d92` → `var(--brand-pink)`

## Technical Notes

### Theme Implementation

Custom theme hook in `lib/theme.ts` applies `.light` class to document root:

```typescript
const [theme, setTheme] = useState<Theme>('system');

useEffect(() => {
  const root = document.documentElement;
  if (theme === 'light' || (theme === 'system' && prefersDark === false)) {
    root.classList.add('light');
  } else {
    root.classList.remove('light');
  }
}, [theme, prefersDark]);
```

### CSS Variables Pattern

```css
:root {
  --text: #ffffff;
  --text-muted: rgba(255, 255, 255, 0.6);
  --card: rgba(255, 255, 255, 0.05);
}

.light {
  --text: #1a1a1a;
  --text-muted: rgba(0, 0, 0, 0.6);
  --card: rgba(0, 0, 0, 0.03);
}
```

### Screenshot Optimization

Converted user screenshots to AVIF format:
- Original JPEGs: 2.8MB total
- AVIF output: 104KB total (96% reduction)

## Testing

- Verified light and dark mode across all pages
- Tested theme toggle persistence
- Checked responsive layout on mobile viewports
- Manual testing of screenshots

## Related PRs

- Part of PR #77 branch `feat/sess-20260107-2130-sync-ui-branding`

## Commits

1. `feat: sync UI with updated brand style guide from Generator`
2. `fix: address CodeRabbit review feedback`
3. `feat: align homepage layout with Generator styling`
4. `fix: make header transparent with blur, fix CodeRabbit issues`
5. `feat: overhaul homepage content with problem-focused approach`
6. `refactor: simplify hero section and update homepage content`
7. `feat: update screenshots with better images and convert to AVIF`
8. `fix: replace hardcoded colors with CSS variables for light mode support`
9. `fix: additional light mode color fixes across 10 files`
