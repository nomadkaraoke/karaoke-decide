"use client"

import { Moon, Sun } from "lucide-react"
import { useTranslations } from "next-intl"
import { useTheme } from "@/lib/theme"

export function ThemeToggle() {
  const { theme, toggleTheme, mounted } = useTheme()
  const t = useTranslations('common')

  // Avoid hydration mismatch by not rendering until mounted
  if (!mounted) {
    return (
      <button
        className="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-black/10 dark:hover:bg-[var(--secondary)] transition-colors"
        aria-label={t('toggleTheme')}
      >
        <span className="sr-only">{t('toggleTheme')}</span>
      </button>
    )
  }

  return (
    <button
      onClick={toggleTheme}
      className="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-black/10 dark:hover:bg-[var(--secondary)] transition-colors"
      title={theme === 'dark' ? t('switchToLight') : t('switchToDark')}
      aria-label={t('toggleTheme')}
    >
      {theme === "dark" ? (
        <Sun className="h-5 w-5 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors" />
      ) : (
        <Moon className="h-5 w-5 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors" />
      )}
      <span className="sr-only">{t('toggleTheme')}</span>
    </button>
  )
}
