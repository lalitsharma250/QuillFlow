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

// Response interceptor — handle 401 + auto-refresh
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    const status = error.response?.status

    // ── 401: Try refresh token flow (existing logic) ──
    if (status === 401 && !originalRequest._retry) {
      originalRequest._retry = true

      const refreshToken = useAuthStore.getState().refreshToken
        
      if (refreshToken) {
        try {
          const response = await axios.post(`${API_URL}/v1/auth/refresh`, {
            refresh_token: refreshToken,
          })

          const newAccessToken = response.data.access_token
          useAuthStore.getState().setAccessToken(newAccessToken)

          originalRequest.headers.Authorization = `Bearer ${newAccessToken}`
          return apiClient(originalRequest)
        } catch (refreshError) {
          useAuthStore.getState().logout()
          window.location.href = '/login'
          return Promise.reject(refreshError)
        }
      } else {
        useAuthStore.getState().logout()
        window.location.href = '/login'
      }
    }

    // ── 403: Permission denied (role changed, token has old role) ──
    if (status === 403) {
      const detail = error.response?.data?.detail || ''
      
      // Check if error suggests role/auth issue (not business logic 403)
      const isAuthIssue = 
        detail.includes('role') || 
        detail.includes('permission') ||
        detail.includes('invalid') ||
        detail.includes('expired')
      
      if (isAuthIssue && !window.location.pathname.includes('/login')) {
        // Force re-auth to get fresh token with current role
        useAuthStore.getState().logout()
        window.location.href = '/login?reason=role_changed'
      }
    }

    return Promise.reject(error)
  }
)

export default apiClient