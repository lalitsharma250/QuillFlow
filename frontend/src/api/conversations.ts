import apiClient from './client'

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface MessageData {
  id: string
  role: string
  content: string
  sources: any[]
  query_type: string | null
  cached: boolean
  created_at: string
}

export interface ConversationDetail extends ConversationSummary {
  messages: MessageData[]
}

export const conversationsApi = {
  list: async (): Promise<ConversationSummary[]> => {
    const response = await apiClient.get('/v1/conversations')
    return response.data
  },

  get: async (id: string): Promise<ConversationDetail> => {
    const response = await apiClient.get(`/v1/conversations/${id}`)
    return response.data
  },

  create: async (title = 'New Chat'): Promise<ConversationSummary> => {
    const response = await apiClient.post('/v1/conversations', { title })
    return response.data
  },

  rename: async (id: string, title: string): Promise<ConversationSummary> => {
    const response = await apiClient.patch(`/v1/conversations/${id}`, { title })
    return response.data
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/v1/conversations/${id}`)
  },
}