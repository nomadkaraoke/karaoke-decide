"use client"

import { Moon, Sun } from "lucide-react"
import { useTheme } from "@/lib/theme"

export function ThemeToggle() {
  const { theme, toggleTheme, mounted } = useTheme()

  // Avoid hydration mismatch by not rendering until mounted
  if (!mounted) {
    return (
      <button
        className="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-white/10 transition-colors"
        aria-label="Toggle theme"
      >
        <span className="sr-only">Toggle theme</span>
      </button>
    )
  }

  return (
    <button
      onClick={toggleTheme}
      className="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-white/10 dark:hover:bg-white/10 light:hover:bg-black/10 transition-colors"
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      aria-label="Toggle theme"
    >
      {theme === "dark" ? (
        <Sun className="h-5 w-5 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors" />
      ) : (
        <Moon className="h-5 w-5 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors" />
      )}
      <span className="sr-only">Toggle theme</span>
    </button>
  )
}
