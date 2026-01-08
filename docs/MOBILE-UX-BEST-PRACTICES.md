# Mobile UX Best Practices

This document outlines the mobile-first UX principles and implementation patterns used in this project to ensure a great experience across all screen sizes.

## Core Principles

### 1. Mobile-First Responsive Design

Design for the smallest screen first, then progressively enhance for larger screens.

```css
/* Base styles for mobile */
.element {
  width: 100%;
  padding: 12px;
}

/* Enhance for larger screens */
@media (min-width: 640px) {
  .element {
    width: 280px;
    padding: 16px;
  }
}
```

### 2. No Horizontal Overflow

The most critical mobile UX issue is horizontal scrolling. Always ensure content fits within the viewport width.

**Testing for overflow:**
```typescript
async function hasNoHorizontalOverflow(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    const docWidth = document.documentElement.scrollWidth;
    const viewportWidth = window.innerWidth;
    return docWidth <= viewportWidth + 2; // Small tolerance for rounding
  });
}
```

**Common causes of horizontal overflow:**
- Fixed widths (e.g., `width: 340px`)
- Horizontal flex layouts without wrapping
- Images without `max-width: 100%`
- Tables without horizontal scroll containers

### 3. Touch Target Sizing

Interactive elements must be easily tappable. Follow these minimum sizes:

| Platform | Minimum Size | Recommended |
|----------|--------------|-------------|
| Apple iOS | 44x44px | 48x48px |
| Material Design | 48x48px | 48x48px |
| Web (general) | 32x32px | 44x44px |

**Implementation:**
```tsx
<Button className="min-h-[44px] px-3">
  <Icon className="w-4 h-4" />
  <span className="hidden sm:inline">Label</span>
</Button>
```

### 4. Progressive Disclosure

Show less content on mobile, with clear paths to access more:

- **Icon-only buttons on mobile, labels on desktop:**
  ```tsx
  <Icon className="w-4 h-4 sm:mr-2" />
  <span className="hidden sm:inline">Action Label</span>
  ```

- **Collapsed navigation on mobile**
- **Truncated text with expansion options**

## Layout Patterns

### Responsive Grid

Use responsive grid columns that stack on mobile:

```tsx
// Bad - always 2 columns
<div className="grid grid-cols-2 gap-4">

// Good - stack on mobile, 2 columns on larger screens
<div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
```

### Flexible Containers

Avoid fixed widths. Use percentage-based or responsive widths:

```css
/* Bad */
.sidebar {
  width: 340px;
}

/* Good */
.sidebar {
  width: 100%;
}

@media (min-width: 768px) {
  .sidebar {
    width: 340px;
  }
}
```

### Flex Wrap for Toolbars

Allow toolbar items to wrap on smaller screens:

```tsx
<div className="flex flex-wrap gap-2 items-center">
  {/* Items will wrap naturally */}
</div>
```

## Typography

### Readable Font Sizes

Minimum font sizes for readability without zooming:

| Element | Minimum Size | Recommended |
|---------|--------------|-------------|
| Body text | 14px | 16px |
| Labels | 12px | 14px |
| Small text/badges | 11px | 12px |

### Responsive Headings

```tsx
<h1 className="text-base sm:text-xl font-bold truncate">
  Title Text
</h1>
```

## Form Design

### Input Fields on Mobile

- Stack form fields vertically on mobile
- Use full-width inputs for easier tapping
- Ensure proper input type for mobile keyboard optimization

```tsx
<input
  type="email"  // Shows email keyboard
  inputMode="numeric"  // Shows number pad
  className="w-full min-h-[44px]"
/>
```

### Labels and Placeholders

- Labels should be visible (not just placeholders)
- Adequate spacing between label and input
- Clear validation feedback

## Testing Strategy

### Automated Testing with Playwright

Test across multiple viewport sizes representing real devices:

```typescript
const MOBILE_VIEWPORTS = {
  'iPhone SE': { width: 375, height: 667 },
  'iPhone 14': { width: 390, height: 844 },
  'Pixel 7': { width: 412, height: 915 },
  'iPad Mini': { width: 768, height: 1024 },
};

for (const [name, viewport] of Object.entries(MOBILE_VIEWPORTS)) {
  test(`page works on ${name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/');

    // Test for horizontal overflow
    const hasOverflow = await page.evaluate(() =>
      document.documentElement.scrollWidth > window.innerWidth + 2
    );
    expect(hasOverflow).toBe(false);
  });
}
```

### Visual Regression Testing

Capture screenshots at different viewports to catch visual regressions:

```typescript
await expect(page).toHaveScreenshot(`homepage-${deviceName}.png`, {
  fullPage: true,
  maxDiffPixelRatio: 0.1,
});
```

### Touch Target Validation

```typescript
const buttons = page.locator('button, a, [role="button"]');
for (const button of await buttons.all()) {
  const box = await button.boundingBox();
  if (box) {
    expect(box.width).toBeGreaterThanOrEqual(32);
    expect(box.height).toBeGreaterThanOrEqual(32);
  }
}
```

## Framework-Specific Guidelines

### Tailwind CSS (Next.js Frontend)

Use responsive prefixes consistently:

```plaintext
sm: 640px    - Small tablets and up
md: 768px    - Tablets and up
lg: 1024px   - Laptops and up
xl: 1280px   - Desktops and up
```

**Common patterns:**
```tsx
// Stack on mobile, side-by-side on larger
className="flex flex-col sm:flex-row"

// Hide on mobile, show on larger
className="hidden sm:block"

// Reduce padding on mobile
className="p-3 sm:p-4"

// Smaller font on mobile
className="text-xs sm:text-sm"
```

### Material UI (Lyrics Review UI)

Use the `useMediaQuery` hook:

```tsx
const theme = useTheme();
const isMobile = useMediaQuery(theme.breakpoints.down('md'));

return (
  <Box sx={{
    flexDirection: isMobile ? 'column' : 'row',
    width: isMobile ? '100%' : '280px',
  }}>
```

### Vanilla CSS (Instrumental Review)

Use CSS media queries:

```css
.element {
  width: 100%;
}

@media (min-width: 768px) {
  .element {
    width: 340px;
  }
}
```

## Checklist for New Features

Before merging any UI changes, verify:

- [ ] No horizontal overflow on any mobile viewport (320px - 480px)
- [ ] All interactive elements are at least 44x44px
- [ ] Text is readable without zooming (minimum 11px)
- [ ] Form inputs stack vertically on mobile
- [ ] Navigation is accessible on mobile
- [ ] Images scale properly with `max-width: 100%`
- [ ] Modals/dialogs fit within mobile viewport
- [ ] Buttons show icons-only on very small screens
- [ ] Playwright mobile tests pass

## Common Fixes

### Fix: Horizontal Overflow

1. **Check for fixed widths** - Replace with responsive values
2. **Add flex-wrap** - Allow content to wrap
3. **Add overflow-x: hidden** - On containers (use sparingly)
4. **Check images** - Add `max-width: 100%`

### Fix: Touch Target Too Small

1. **Add minimum height** - `min-h-[44px]`
2. **Add padding** - `p-3` or `px-4 py-3`
3. **Use icon-only on mobile** - Hide text labels

### Fix: Text Too Small

1. **Use responsive font sizes** - `text-xs sm:text-sm`
2. **Set minimum body font size** - At least 14px
3. **Increase line height** - For better readability

## Resources

- [Apple Human Interface Guidelines - Touch Targets](https://developer.apple.com/design/human-interface-guidelines/accessibility)
- [Material Design - Touch Targets](https://m3.material.io/foundations/accessible-design/accessibility-basics)
- [Tailwind CSS Responsive Design](https://tailwindcss.com/docs/responsive-design)
- [Playwright Testing](https://playwright.dev/docs/test-mobile)
