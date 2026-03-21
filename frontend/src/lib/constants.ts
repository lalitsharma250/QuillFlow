export const API_URL = (import.meta as any).env?.VITE_API_URL || ''

export const ROUTES = {
  LOGIN: '/login',
  CHAT: '/',
  DOCUMENTS: '/documents',
  ADMIN: '/admin',
} as const

export const ROLES = {
  ADMIN: 'admin',
  EDITOR: 'editor',
  VIEWER: 'viewer',
} as const