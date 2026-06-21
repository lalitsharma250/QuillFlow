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

// Initialize theme on load. The inline script in index.html applies the class
// before first paint; this keeps the document class in sync with the store on
// hydration and applies BOTH branches (the old version only ever added 'light').
export function initializeTheme() {
  const theme = useThemeStore.getState().theme
  const el = document.documentElement
  el.classList.remove('light', 'dark')
  el.classList.add(theme)
}