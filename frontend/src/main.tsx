import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { initializeTheme } from '@/stores/themeStore'
import './index.css'

// Apply saved theme before render (prevents flash)
initializeTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)