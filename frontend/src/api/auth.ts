import apiClient from './client'
import type { TokenResponse, InviteVerifyResponse } from '@/lib/types'

export const authApi = {
  login: async (email: string, password: string): Promise<TokenResponse> => {
    const response = await apiClient.post('/v1/auth/login', { email, password })
    return response.data
  },

  signup: async (data: {
    email: string
    password: string
    name: string
    invite_code: string
  }): Promise<TokenResponse> => {
    const response = await apiClient.post('/v1/auth/signup', data)
    return response.data
  },

  loginWithApiKey: async (apiKey: string): Promise<TokenResponse> => {
    const response = await apiClient.post('/v1/auth/login/key', { api_key: apiKey })
    return response.data
  },

  refresh: async (refreshToken: string) => {
    const response = await apiClient.post('/v1/auth/refresh', {
      refresh_token: refreshToken,
    })
    return response.data
  },

  verifyInvite: async (code: string): Promise<InviteVerifyResponse> => {
    const response = await apiClient.get('/v1/auth/invite/verify', {
      params: { code },
    })
    return response.data
  },

  getMe: async () => {
    const response = await apiClient.get('/v1/auth/me')
    return response.data
  },
}