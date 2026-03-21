import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface ThemeState {
  theme: 'dark' | 'light'
  toggleTheme: () => void
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: 'dark',
      toggleTheme: () => {
        const newTheme = get().theme === 'dark' ? 'light' : 'dark'
        set({ theme: newTheme })

        // Apply to document
        if (newTheme === 'light') {
          document.documentElement.classList.add('light')
          document.documentElement.classList.remove('dark')
        } else {
          document.documentElement.classList.add('dark')
          document.documentElement.classList.remove('light')
        }
      },
    }),
    {
      name: 'quillflow-theme',
    }
  )
)

// Initialize theme on load
export function initializeTheme() {
  const stored = localStorage.getItem('quillflow-theme')
  if (stored) {
    const parsed = JSON.parse(stored)
    if (parsed.state?.theme === 'light') {
      document.documentElement.classList.add('light')
    }
  }
}