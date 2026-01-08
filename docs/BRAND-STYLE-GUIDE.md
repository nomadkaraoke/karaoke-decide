# Nomad Karaoke Brand Style Guide

> **Version**: 1.1
> **Last Updated**: 2026-01-07
> **Purpose**: Unified brand identity for all Nomad Karaoke products and communications

This style guide is designed for both human designers and LLM agents implementing the Nomad Karaoke brand across web applications, emails, marketing materials, and other touchpoints.

---

## Table of Contents

1. [Brand Overview](#brand-overview)
2. [Color Palette](#color-palette)
3. [Typography](#typography)
4. [Logo Usage](#logo-usage)
5. [Iconography](#iconography)
6. [UI Components](#ui-components)
7. [Layout & Spacing](#layout--spacing)
8. [Email Design](#email-design)
9. [Tone & Voice](#tone--voice)
10. [Light/Dark Theme Requirements](#lightdark-theme-requirements)
11. [Implementation Reference](#implementation-reference)

---

## Brand Overview

### Brand Personality
Nomad Karaoke is **approachable**, **professional**, and **modern**. We make sophisticated technology feel accessible and fun. Our brand bridges the gap between serious audio engineering and the joy of karaoke.

### Core Values
- **Approachable**: Technology that anyone can use
- **Professional**: Studio-quality output
- **Playful**: Karaoke should be fun
- **Trustworthy**: Reliable service and results

### Brand Keywords
`modern` `vibrant` `accessible` `professional` `musical` `AI-powered` `creative`

---

## Color Palette

### Primary Colors

| Name | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Hot Pink** | `#ff7acc` | rgb(255, 122, 204) | Primary brand color, logo, primary buttons, key accents |
| **Gold** | `#ffdf6b` | rgb(255, 223, 107) | Secondary accent, highlights, success states, premium feel |

### Supporting Colors

| Name | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Deep Purple** | `#8b5cf6` | rgb(139, 92, 246) | Gradients, special features, beta badges |
| **Electric Blue** | `#3b82f6` | rgb(59, 130, 246) | Links, interactive elements, info states |
| **Soft Pink** | `#ec4899` | rgb(236, 72, 153) | Gradient end points, hover states |

### Semantic Colors

| Name | Hex | Usage |
|------|-----|-------|
| **Success** | `#22c55e` | Completed states, positive feedback |
| **Warning** | `#f59e0b` | Attention needed, caution states |
| **Error** | `#ef4444` | Errors, destructive actions |
| **Info** | `#3b82f6` | Informational messages |

### Dark Theme (Default)

| Name | Hex | Usage |
|------|-----|-------|
| **Background** | `#0f0f0f` | Page background |
| **Card Background** | `#1a1a1a` | Card surfaces |
| **Card Border** | `#2a2a2a` | Subtle borders |
| **Text Primary** | `#e5e5e5` | Main content text |
| **Text Muted** | `#888888` | Secondary text, labels |
| **Text Subtle** | `#666666` | Tertiary text, hints |

### Light Theme

| Name | Hex | Usage |
|------|-----|-------|
| **Background** | `#f8fafc` | Page background |
| **Card Background** | `#ffffff` | Card surfaces |
| **Card Border** | `#e2e8f0` | Subtle borders |
| **Text Primary** | `#1e293b` | Main content text |
| **Text Muted** | `#64748b` | Secondary text, labels |

### Gradients

#### Primary Gradient (Brand Gradient)
```css
background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 50%, #ff7acc 100%);
```
Use for: Hero text, feature highlights, premium elements. The gradient flows from blue through purple to the brand pink.

#### Radial Background Gradient (Landing Pages)
```css
/* Layered radial gradients using brand colors */
body::after {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -1;
  background:
    radial-gradient(ellipse at 20% 0%, rgba(139, 92, 246, 0.12) 0%, transparent 50%),   /* Purple glow top-left */
    radial-gradient(ellipse at 80% 100%, rgba(255, 122, 204, 0.10) 0%, transparent 50%), /* Pink glow bottom-right */
    radial-gradient(ellipse at 50% 50%, rgba(255, 223, 107, 0.03) 0%, transparent 70%);  /* Gold subtle center */
  pointer-events: none;
}
```
Use for: Landing page backgrounds, hero sections. Creates depth with brand colors without being distracting.

**Light theme variant**: Reduce opacity values (0.08, 0.06, 0.02) for subtler effect on light backgrounds.

#### Beta/Special Gradient
```css
background: linear-gradient(135deg, #8b5cf6, #ec4899);
```
Use for: Beta badges, special promotions, exclusive features.

---

## Typography

### Font Stack

#### Primary (Sans-serif)
```css
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
```
Use system fonts for optimal performance and native feel.

#### Monospace (Code/Technical)
```css
font-family: "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", monospace;
```
Use for: Code snippets, technical information, job IDs.

### Type Scale

| Name | Size | Weight | Line Height | Usage |
|------|------|--------|-------------|-------|
| **Display** | 48-60px | 800 (extrabold) | 1.1 | Hero headlines |
| **H1** | 36-48px | 700 (bold) | 1.2 | Page titles |
| **H2** | 28-32px | 700 (bold) | 1.3 | Section headers |
| **H3** | 20-24px | 600 (semibold) | 1.4 | Card titles |
| **Body** | 16px | 400 (regular) | 1.6 | Main content |
| **Body Small** | 14px | 400 (regular) | 1.5 | Secondary content |
| **Caption** | 12px | 400 (regular) | 1.4 | Labels, hints |

### Text Styling

- **Gradient Text**: Apply brand gradient to hero headlines
- **Antialiasing**: Always use `-webkit-font-smoothing: antialiased`
- **Letter Spacing**: Default, slightly increased for ALL CAPS text

---

## Logo Usage

**Important**: Always use the Nomad Karaoke logo rather than generic emojis (like microphone emoji) in headers, navigation, and brand touchpoints.

### Primary Logo
The Nomad Karaoke logo consists of:
1. **Wordmark**: "NOMAD" stylized letters (pink #ff7acc)
2. **Tagline**: "KARAOKE" below (pink #ff7acc)
3. **Subline**: "WHERE EVERY SONG'S YOURS, SINGING!" (gold #ffdf6b)

### Logo Format Priority

1. **SVG (Preferred)**: Use `nomad-karaoke-logo.svg` for all web applications
   - Vector format scales perfectly at any size
   - Smaller file size
   - Supports both light and dark themes
   - Located in: `frontend/public/` directory

2. **PNG**: Use for contexts that don't support SVG
   - `nomad-logo.png` - Standard PNG fallback

3. **GIF (Email Only)**: Use the hosted GIF **only** for email templates
   - URL: `https://beveradb.github.io/public-images/Nomad-Karaoke-Logo-small-indexed-websafe-rectangle.gif`
   - This hosted URL exists because emails cannot embed local assets
   - Do NOT use this URL in web applications - use the local SVG instead

### Logo Files
- `nomad-karaoke-logo.svg` - Full vector logo (**primary choice**)
- `nomad-logo.png` - PNG fallback
- `favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png` - Favicons
- `apple-touch-icon.png` - iOS home screen icon

### Logo Placement
- **Navigation**: Left-aligned, 32-40px height, use SVG
- **Footer**: Smaller, 24-32px height, use SVG
- **Email Header**: Centered, 130-180px width, use hosted GIF URL

### Logo Clear Space
Maintain clear space equal to the "N" letter height around all sides of the logo.

### Logo Don'ts
- Don't use emojis instead of the logo
- Don't change the logo colors
- Don't stretch or distort the logo
- Don't add effects (shadows, gradients, etc.)
- Don't place on busy backgrounds without contrast
- Don't use the logo smaller than 24px height
- Don't use the hosted GIF URL in web applications (use SVG)

---

## Iconography

### Icon Library
Use [Lucide Icons](https://lucide.dev/) for UI icons. They are:
- Open source
- Consistent stroke width
- Good visual balance

### Common Icons

| Icon | Name | Usage |
|------|------|-------|
| `MicVocal` | Microphone | Brand representation in nav (when logo not used) |
| `Music` | Music note | Audio/song related |
| `Music2` | Double notes | Empty states for songs |
| `Sparkles` | Stars | AI/magic features |
| `Video` | Video | Video output |
| `Youtube` | YouTube logo | YouTube integration |
| `Gift` | Gift box | Free credits, promotions |
| `Check` | Checkmark | Success, confirmation |
| `Loader2` | Spinner | Loading states |

### Icon Sizing
- **Small**: 16px (inline with text)
- **Medium**: 20-24px (buttons, lists)
- **Large**: 32-48px (feature highlights)
- **XL**: 64px+ (empty states, hero)

### Icon Colors
- Match text color in context
- Use brand pink (#ff7acc) for emphasis
- Use muted color for decorative icons

---

## UI Components

### Buttons

#### Primary Button (CTA)
```css
background-color: #ff7acc;
color: #ffffff;
padding: 14px 28px;
border-radius: 12px;
font-weight: 600;
box-shadow: 0 0 20px rgba(255, 122, 204, 0.4);
transition: all 0.2s ease;

/* Hover */
background-color: #ff5bb8;
box-shadow: 0 0 30px rgba(255, 122, 204, 0.6);
```

#### Secondary Button
```css
background-color: #252525;
color: #e5e5e5;
padding: 12px 24px;
border-radius: 8px;
border: 1px solid #2a2a2a;

/* Hover */
background-color: #333333;
```

#### Ghost Button
```css
background-color: transparent;
color: #888888;
padding: 8px 16px;

/* Hover */
color: #e5e5e5;
background-color: rgba(255, 255, 255, 0.05);
```

### Cards

```css
background-color: var(--card); /* #1a1a1a dark / #ffffff light */
border: 1px solid var(--card-border); /* #2a2a2a dark / #e2e8f0 light */
border-radius: 12px;
padding: 24px;

/* Hover effect (optional) */
transition: transform 0.2s ease, box-shadow 0.2s ease;
transform: translateY(-4px);
box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
```

### Form Inputs

```css
background-color: #0f0f0f; /* Darker than card */
border: 1px solid #2a2a2a;
border-radius: 8px;
padding: 12px 16px;
color: #e5e5e5;

/* Focus */
border-color: #ff7acc;
box-shadow: 0 0 0 2px rgba(255, 122, 204, 0.2);
outline: none;

/* Placeholder */
color: #666666;
```

### Badges

#### Beta Badge
```css
background: linear-gradient(135deg, #8b5cf6, #ec4899);
color: #ffffff;
padding: 4px 12px;
border-radius: 20px;
font-size: 12px;
font-weight: 600;
```

#### Status Badges
Use semantic colors with 20% opacity backgrounds:
- Success: `background: rgba(34, 197, 94, 0.2); color: #22c55e;`
- Warning: `background: rgba(245, 158, 11, 0.2); color: #f59e0b;`
- Error: `background: rgba(239, 68, 68, 0.2); color: #ef4444;`

---

## Layout & Spacing

### Spacing Scale
Use a consistent 4px base unit:

| Name | Value | Usage |
|------|-------|-------|
| `xs` | 4px | Tight spacing |
| `sm` | 8px | Small gaps |
| `md` | 16px | Default gaps |
| `lg` | 24px | Section padding |
| `xl` | 32px | Large sections |
| `2xl` | 48px | Major sections |
| `3xl` | 64px | Page sections |

### Container Widths
- **Max content width**: 1200px (6xl)
- **Text content**: 672px (2xl) for readability
- **Form width**: 448px (md) for focused input

### Grid
Use CSS Grid or Flexbox with responsive breakpoints:
- Mobile: 1 column
- Tablet (md: 768px): 2 columns
- Desktop (lg: 1024px): 2-4 columns

### Border Radius
- **Small** (inputs, small buttons): 8px
- **Medium** (cards, buttons): 12px
- **Large** (feature cards, modals): 16px
- **Pill** (badges, tags): 20px or 9999px

---

## Email Design

### Email Structure

```html
<!DOCTYPE html>
<html>
<head>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      line-height: 1.6;
      color: #333333;
      max-width: 600px;
      margin: 0 auto;
      padding: 20px;
      background-color: #ffffff;
    }
  </style>
</head>
<body>
  <!-- Header with logo -->
  <div style="text-align: center; padding: 20px 0;">
    <img src="logo-url" alt="Nomad Karaoke" width="150" />
  </div>

  <!-- Content -->
  <div>...</div>

  <!-- Footer -->
  <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ff7acc; font-size: 12px; color: #666;">
    ...
  </div>
</body>
</html>
```

### Email Colors
- **Background**: `#ffffff` (white for email client compatibility)
- **Text**: `#333333`
- **Primary Button**: `#ff7acc` with white text
- **Links**: `#3b82f6`
- **Dividers**: `#ff7acc` (brand pink) or `#eeeeee`
- **Footer text**: `#666666`

### Email Button
```html
<a href="URL" style="display: inline-block; background-color: #ff7acc; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: 600;">
  Button Text
</a>
```

### Email Header
Always use the Nomad Karaoke logo image in email headers, not an emoji.

```html
<div style="text-align: center; padding: 20px 0;">
  <a href="https://nomadkaraoke.com">
    <img src="https://beveradb.github.io/public-images/Nomad-Karaoke-Logo-small-indexed-websafe-rectangle.gif"
         alt="Nomad Karaoke"
         style="max-width: 180px; height: auto; border-radius: 10px;" />
  </a>
</div>
```

### Email Signature
Include consistent signature with:
- Profile image
- Name and title
- Social media icons
- Contact information
- Pink (#ff7acc) accent lines

---

## Tone & Voice

### Writing Style
- **Friendly but professional**: "Hi there!" not "Dear Customer"
- **Clear and concise**: Get to the point
- **Encouraging**: Celebrate user accomplishments
- **Helpful**: Guide users through actions

### Example Phrases

| Instead of... | Use... |
|---------------|--------|
| "Your order has been processed" | "Your karaoke video is ready!" |
| "Error occurred" | "Something went wrong - we're on it!" |
| "Please wait" | "Just a moment..." |
| "Submit" | "Create Karaoke" or "Get Started" |

### Emoji Usage
- Use sparingly for warmth
- Common: microphone (brand), music note (music), sparkles (magic), party popper (celebration)
- Avoid in error messages
- Email subject lines: One emoji max

---

## Light/Dark Theme Requirements

### Overview
All Nomad Karaoke web application screens must support both light and dark themes. Dark theme is the default, but users should have easy access to toggle between themes.

### Implementation Requirements

1. **Theme Toggle**: Every screen/page must include a visible theme toggle
   - Location: Typically in the header or settings area
   - Use `next-themes` library for Next.js applications
   - Toggle should be easily discoverable

2. **Default Theme**: Dark mode
   - Respects system preference on first visit
   - User preference is persisted

3. **Color Consistency**: All brand colors must work in both themes
   - Brand pink (#ff7acc) remains consistent
   - Background, card, and text colors adapt per theme tables above
   - Ensure sufficient contrast in both modes

### Theme Implementation (Custom Hook)

We use a custom `useTheme` hook with localStorage persistence:

```tsx
// lib/theme.ts - Custom theme hook
"use client"
import { useState, useEffect, useCallback } from "react"

const THEME_STORAGE_KEY = "nomad-karaoke-theme"
export type Theme = "dark" | "light"

function applyTheme(newTheme: Theme) {
  const root = document.documentElement
  if (newTheme === "light") {
    root.classList.add("light")
  } else {
    root.classList.remove("light")
  }
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("dark")
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    const stored = localStorage.getItem(THEME_STORAGE_KEY) as Theme | null
    if (stored === "light" || stored === "dark") {
      setThemeState(stored)
      applyTheme(stored)
    } else {
      applyTheme("dark")
    }
  }, [])

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme)
    localStorage.setItem(THEME_STORAGE_KEY, newTheme)
    applyTheme(newTheme)
  }, [])

  const toggleTheme = useCallback(() => {
    const newTheme = theme === "dark" ? "light" : "dark"
    setTheme(newTheme)
  }, [theme, setTheme])

  return { theme, setTheme, toggleTheme, mounted }
}

// components/ThemeToggle.tsx - Theme toggle component
import { Moon, Sun } from "lucide-react"
import { useTheme } from "@/lib/theme"

export function ThemeToggle() {
  const { theme, toggleTheme, mounted } = useTheme()

  if (!mounted) return <button className="w-9 h-9" aria-label="Toggle theme" />

  return (
    <button
      onClick={toggleTheme}
      className="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-black/10 dark:hover:bg-white/10"
      aria-label="Toggle theme"
    >
      {theme === "dark" ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
    </button>
  )
}
```

### CSS Variables for Theming

```css
/* Dark theme (default) */
:root {
  --background: #0f0f0f;
  --card: #1a1a1a;
  --card-border: #2a2a2a;
  --text: #e5e5e5;
  --text-muted: #888888;
}

/* Light theme */
.light {
  --background: #f8fafc;
  --card: #ffffff;
  --card-border: #e2e8f0;
  --text: #1e293b;
  --text-muted: #64748b;
}

/* Brand colors stay consistent */
:root {
  --brand-pink: #ff7acc;
  --brand-gold: #ffdf6b;
  --brand-purple: #8b5cf6;
}
```

### Testing Checklist
- [ ] Theme toggle is visible on all pages
- [ ] Toggle persists preference across sessions
- [ ] All text is readable in both themes
- [ ] Brand colors render correctly in both themes
- [ ] Interactive elements have proper hover/focus states in both themes
- [ ] Radial background gradient adjusts opacity for light theme

---

## Implementation Reference

### CSS Variables (Recommended)

```css
:root {
  /* Brand Colors */
  --brand-pink: #ff7acc;
  --brand-pink-hover: #ff5bb8;
  --brand-gold: #ffdf6b;
  --brand-purple: #8b5cf6;
  --brand-blue: #3b82f6;

  /* Semantic Colors */
  --success: #22c55e;
  --warning: #f59e0b;
  --danger: #ef4444;

  /* Dark Theme */
  --bg: #0f0f0f;
  --card: #1a1a1a;
  --card-border: #2a2a2a;
  --text: #e5e5e5;
  --text-muted: #888888;

  /* Spacing */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;

  /* Border Radius */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-pill: 9999px;
}
```

### Tailwind Configuration

```javascript
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        brand: {
          pink: '#ff7acc',
          'pink-hover': '#ff5bb8',
          gold: '#ffdf6b',
          purple: '#8b5cf6',
          blue: '#3b82f6',
        },
      },
    },
  },
}
```

### React Component Example

```tsx
// Primary Button Component
export function PrimaryButton({ children, ...props }) {
  return (
    <button
      className="bg-brand-pink hover:bg-brand-pink-hover text-white font-semibold px-7 py-3.5 rounded-xl transition-all shadow-[0_0_20px_rgba(255,122,204,0.4)] hover:shadow-[0_0_30px_rgba(255,122,204,0.6)]"
      {...props}
    >
      {children}
    </button>
  )
}
```

---

## Checklist for Brand Compliance

### Landing Pages
- [ ] Uses brand pink (#ff7acc) for primary CTAs
- [ ] Logo displayed correctly
- [ ] Dark theme as default
- [ ] Gradient text on hero headlines
- [ ] Consistent typography scale
- [ ] Proper spacing (multiples of 4px)

### Application UI
- [ ] Logo in header (32-40px height)
- [ ] Brand pink for primary actions
- [ ] Consistent card styling
- [ ] Proper focus states with brand pink
- [ ] Dark/light theme toggle working

### Emails
- [ ] Header with logo image (not emoji)
- [ ] Brand pink buttons
- [ ] Pink divider lines
- [ ] Consistent footer with signature
- [ ] White background for compatibility

### Marketing Materials
- [ ] Logo with proper clear space
- [ ] Brand colors only
- [ ] Consistent typography
- [ ] No logo modifications

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.1 | 2026-01-07 | Updated radial background gradient (now uses layered radial gradients with optional animation); Updated logo usage (SVG preferred, hosted GIF URL for emails only); Updated email header (use logo image, not emoji); Added Light/Dark Theme Requirements section; Fixed success color consistency (#22c55e) |
| 1.0 | 2026-01-07 | Initial brand guide created |

---

*This guide is the source of truth for Nomad Karaoke brand identity. For questions or clarifications, contact the design team.*
