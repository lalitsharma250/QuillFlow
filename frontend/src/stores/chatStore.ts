import { create } from "zustand"
import { conversationsApi, type ConversationSummary, type MessageData } from "@/api/conversations"
import type { ChatMessage } from "@/lib/types"

interface ChatState {
  conversations: ConversationSummary[]
  activeConversationId: string | null
  messages: ChatMessage[]
  loadingConversations: boolean
  loadingMessages: boolean

  // Actions
  loadConversations: () => Promise<void>
  selectConversation: (id: string) => Promise<void>
  startNewChat: () => void
  setActiveConversationId: (id: string | null) => void
  addLocalMessage: (msg: ChatMessage) => void
  deleteConversation: (id: string) => Promise<void>
  clearActive: () => void
  reset: () => void
}

// Convert API message to frontend ChatMessage
function toChatMessage(m: MessageData): ChatMessage {
  return {
    id: m.id,
    role: m.role as "user" | "assistant",
    content: m.content,
    sources: m.sources || [],
    query_type: m.query_type || undefined,
    cached: m.cached,
    created_at: m.created_at,
  }
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  activeConversationId: null,
  messages: [],
  loadingConversations: false,
  loadingMessages: false,

  loadConversations: async () => {
    set({ loadingConversations: true })
    try {
      const convs = await conversationsApi.list()
      set({ conversations: convs })
    } catch (e) {
      console.error("Failed to load conversations:", e)
    } finally {
      set({ loadingConversations: false })
    }
  },

  selectConversation: async (id: string) => {
    set({ loadingMessages: true, activeConversationId: id })
    try {
      const detail = await conversationsApi.get(id)
      set({
        messages: detail.messages.map(toChatMessage),
        activeConversationId: id,
      })
    } catch (e) {
      console.error("Failed to load conversation:", e)
      set({ messages: [] })
    } finally {
      set({ loadingMessages: false })
    }
  },

  startNewChat: () => {
    // Clear active conversation — new one is created on first message
    set({ activeConversationId: null, messages: [] })
  },

  setActiveConversationId: (id) => {
    set({ activeConversationId: id })
  },

  addLocalMessage: (msg) => {
    set((s) => ({ messages: [...s.messages, msg] }))
  },

  deleteConversation: async (id: string) => {
    try {
      await conversationsApi.delete(id)
      set((s) => {
        const remaining = s.conversations.filter((c) => c.id !== id)
        const wasActive = s.activeConversationId === id
        return {
          conversations: remaining,
          activeConversationId: wasActive ? null : s.activeConversationId,
          messages: wasActive ? [] : s.messages,
        }
      })
    } catch (e) {
      console.error("Failed to delete conversation:", e)
    }
  },

  clearActive: () => {
    set({ activeConversationId: null, messages: [] })
  },

  reset: () => {
    set({
      conversations: [],
      activeConversationId: null,
      messages: [],
    })
  },
}))