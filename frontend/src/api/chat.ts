import apiClient from './client'
import { useAuthStore } from '@/stores/authStore'
import type { ChatResponse, StreamEvent } from '@/lib/types'

const API_URL = import.meta.env.VITE_API_URL || ''

export const chatApi = {
  query: async (query: string, history: Array<{role: string; content: string}> = []): Promise<ChatResponse> => {
    const response = await apiClient.post('/v1/chat', {
      query,
      stream: false,
      include_sources: true,
      history,
    })
    return response.data
  },

  streamQuery: (
    query: string,
    history: Array<{role: string; content: string}>,
    onEvent: (event: StreamEvent) => void,
    onError: (error: string) => void,
    onComplete: () => void,
  ): AbortController => {
    const controller = new AbortController()
    const token = useAuthStore.getState().accessToken

    const streamUrl = `${API_URL}/v1/chat`

    fetch(streamUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        'Accept': 'text/event-stream',
      },
      body: JSON.stringify({
        query,
        stream: true,
        include_sources: true,
        history,
      }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const error = await response.json().catch(() => ({ detail: 'Request failed' }))
          onError(error.detail || 'Request failed')
          return
        }

        const reader = response.body?.getReader()
        if (!reader) {
          onError('No response body')
          return
        }

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })

          const parts = buffer.split('\n\n')
          buffer = parts.pop() || ''

          for (const part of parts) {
            const lines = part.split('\n')
            let eventData = ''

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                eventData = line.slice(6).trim()
              }
            }

            if (eventData) {
              try {
                const parsed: StreamEvent = JSON.parse(eventData)
                onEvent(parsed)
              } catch (e) {
                console.warn('Failed to parse SSE event:', eventData)
              }
            }
          }
        }

        onComplete()
      })
      .catch((error) => {
        if (error.name !== 'AbortError') {
          onError(error.message || 'Stream failed')
        }
      })

    return controller
  },
}