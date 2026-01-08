"use client"

import { useState, useEffect, useCallback } from "react"

const THEME_STORAGE_KEY = "nomad-karaoke-theme"

export type Theme = "dark" | "light"

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("dark")
  const [mounted, setMounted] = useState(false)

  // Load theme from localStorage on mount
  useEffect(() => {
    setMounted(true)
    const stored = localStorage.getItem(THEME_STORAGE_KEY) as Theme | null
    if (stored === "light" || stored === "dark") {
      setThemeState(stored)
      applyTheme(stored)
    } else {
      // Apply default theme to DOM when no valid stored value exists
      applyTheme("dark")
    }
  }, [])

  const applyTheme = (newTheme: Theme) => {
    const root = document.documentElement
    if (newTheme === "light") {
      root.classList.add("light")
    } else {
      root.classList.remove("light")
    }
  }

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme)
    localStorage.setItem(THEME_STORAGE_KEY, newTheme)
    applyTheme(newTheme)
  }, [])

  const toggleTheme = useCallback(() => {
    const newTheme = theme === "dark" ? "light" : "dark"
    setTheme(newTheme)
  }, [theme, setTheme])

  const isDarkMode = theme === "dark"

  return {
    theme,
    setTheme,
    toggleTheme,
    isDarkMode,
    mounted,
  }
}
