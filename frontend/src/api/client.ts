import axios from 'axios'
import { useAuthStore } from '@/stores/authStore'

const API_URL = import.meta.env.VITE_API_URL || ''

const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor — attach JWT token
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Helper: derive redirect reason from error detail string
function getReasonFromDetail(detail: string): string {
  if (detail.includes('Role has changed')) return 'role_changed'
  if (detail.includes('Permissions have changed')) return 'permissions_changed'
  if (detail.includes('disabled')) return 'account_disabled'
  return 'session_expired'
}

// Response interceptor — handle 401 + auto-refresh + role staleness
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    const status = error.response?.status
    const detail: string = error.response?.data?.detail || ''

    // ── 401: Decide what to do ──────────────────────────────
    if (status === 401) {
      // Special case: role/permissions/account changed
      // Don't try refresh — token decoded fine, but DB state changed
      const isStaleAuth =
        detail.includes('Role has changed') ||
        detail.includes('Permissions have changed') ||
        detail.includes('disabled')

      if (isStaleAuth) {
        const reason = getReasonFromDetail(detail)
        useAuthStore.getState().logout()
        window.location.href = `/login?reason=${reason}`
        return Promise.reject(error)
      }

      // Otherwise: try refresh token (token expired)
      if (!originalRequest._retry) {
        originalRequest._retry = true

        const refreshToken = useAuthStore.getState().refreshToken

        if (refreshToken) {
          try {
            const response = await axios.post(`${API_URL}/v1/auth/refresh`, {
              refresh_token: refreshToken,
            })

            const newAccessToken = response.data.access_token
            const updatedUser = response.data.user  // ← Get fresh user info
            
            // Update both token and user (in case role changed)
            useAuthStore.getState().setAccessToken(newAccessToken)
            if (updatedUser) {
              useAuthStore.getState().setUser(updatedUser)
            }

            originalRequest.headers.Authorization = `Bearer ${newAccessToken}`
            return apiClient(originalRequest)
          } catch (refreshError: any) {
            // Refresh failed — could be role change OR truly expired refresh
            const refreshDetail: string =
              refreshError.response?.data?.detail || ''
            const reason = getReasonFromDetail(refreshDetail)
            useAuthStore.getState().logout()
            window.location.href = `/login?reason=${reason}`
            return Promise.reject(refreshError)
          }
        } else {
          useAuthStore.getState().logout()
          window.location.href = '/login'
        }
      }
    }

    // ── 403: Permission denied — let component show toast (no redirect) ──
    return Promise.reject(error)
  }
)

export default apiClient