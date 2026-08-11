/* eslint-disable react-refresh/only-export-components */
import * as React from "react"

const COLOR_SCHEME_QUERY = "(prefers-color-scheme: dark)"
const THEME_VALUES = ["dark", "light", "system"]

const ThemeProviderContext = React.createContext(undefined)

function isTheme(value) {
  return value !== null && THEME_VALUES.includes(value)
}

function getSystemTheme() {
  return window.matchMedia(COLOR_SCHEME_QUERY).matches ? "dark" : "light"
}

function disableTransitionsTemporarily() {
  const style = document.createElement("style")
  style.appendChild(
    document.createTextNode(
      "*,*::before,*::after{-webkit-transition:none!important;transition:none!important}"
    )
  )
  document.head.appendChild(style)

  return () => {
    window.getComputedStyle(document.body)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => style.remove())
    })
  }
}

function isEditableTarget(target) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  return Boolean(
    target.isContentEditable ||
      target.closest("input, textarea, select, [contenteditable='true']")
  )
}

export function ThemeProvider({
  children,
  defaultTheme = "system",
  storageKey = "cassist-theme",
  disableTransitionOnChange = true,
  ...props
}) {
  const [theme, setThemeState] = React.useState(() => {
    const storedTheme = localStorage.getItem(storageKey)
    return isTheme(storedTheme) ? storedTheme : defaultTheme
  })

  const setTheme = React.useCallback(
    (nextTheme) => {
      localStorage.setItem(storageKey, nextTheme)
      setThemeState(nextTheme)
    },
    [storageKey]
  )

  const applyTheme = React.useCallback(
    (nextTheme) => {
      const root = document.documentElement
      const resolvedTheme =
        nextTheme === "system" ? getSystemTheme() : nextTheme
      const restoreTransitions = disableTransitionOnChange
        ? disableTransitionsTemporarily()
        : null

      root.classList.remove("light", "dark")
      root.classList.add(resolvedTheme)
      restoreTransitions?.()
    },
    [disableTransitionOnChange]
  )

  React.useEffect(() => {
    applyTheme(theme)

    if (theme !== "system") {
      return undefined
    }

    const mediaQuery = window.matchMedia(COLOR_SCHEME_QUERY)
    const handleChange = () => applyTheme("system")
    mediaQuery.addEventListener("change", handleChange)
    return () => mediaQuery.removeEventListener("change", handleChange)
  }, [theme, applyTheme])

  React.useEffect(() => {
    const handleKeyDown = (event) => {
      if (
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        isEditableTarget(event.target) ||
        event.key.toLowerCase() !== "d"
      ) {
        return
      }

      setThemeState((currentTheme) => {
        const currentResolvedTheme =
          currentTheme === "system" ? getSystemTheme() : currentTheme
        const nextTheme = currentResolvedTheme === "dark" ? "light" : "dark"
        localStorage.setItem(storageKey, nextTheme)
        return nextTheme
      })
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [storageKey])

  React.useEffect(() => {
    const handleStorageChange = (event) => {
      if (event.storageArea !== localStorage || event.key !== storageKey) {
        return
      }
      setThemeState(isTheme(event.newValue) ? event.newValue : defaultTheme)
    }

    window.addEventListener("storage", handleStorageChange)
    return () => window.removeEventListener("storage", handleStorageChange)
  }, [defaultTheme, storageKey])

  const value = React.useMemo(() => ({ theme, setTheme }), [theme, setTheme])

  return (
    <ThemeProviderContext.Provider {...props} value={value}>
      {children}
    </ThemeProviderContext.Provider>
  )
}

export function useTheme() {
  const context = React.useContext(ThemeProviderContext)
  if (!context) {
    throw new Error("useTheme must be used inside ThemeProvider")
  }
  return context
}
