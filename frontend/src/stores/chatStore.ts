import { create } from "zustand"
import { persist } from "zustand/middleware"
import type { ChatMessage } from "@/lib/types"
import { useAuthStore } from "./authStore"

interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  created_at: string
  user_id: string
}

interface ChatState {
  conversations: Conversation[]
  activeConversationId: string | null
  activeConversation: () => Conversation | null
  userConversations: () => Conversation[]
  createConversation: () => string
  setActiveConversation: (id: string) => void
  addMessage: (cid: string, msg: ChatMessage) => void
  updateMessage: (cid: string, mid: string, u: Partial<ChatMessage>) => void
  deleteConversation: (id: string) => void
  clearActive: () => void
  clearAll: () => void
}

const getUserId = () => useAuthStore.getState().user?.user_id || "anon"

export const useChatStore = create<ChatState>()(persist((set, get) => {
  const obj: ChatState = {
    conversations: [],
    activeConversationId: null,

    activeConversation() {
      const s = get()
      const uid = getUserId()
      return s.conversations.find((c) => c.id === s.activeConversationId && c.user_id === uid) || null
    },

    userConversations() {
      const uid = getUserId()
      return get().conversations.filter((c) => c.user_id === uid)
    },

    createConversation() {
      const id = crypto.randomUUID()
      const uid = getUserId()
      const conv: Conversation = { id, title: "New Chat", messages: [], created_at: new Date().toISOString(), user_id: uid }
      set((s) => ({ conversations: [conv, ...s.conversations], activeConversationId: id }))
      return id
    },

    setActiveConversation(id: string) {
      const uid = getUserId()
      const found = get().conversations.find((c) => c.id === id && c.user_id === uid)
      if (found) set({ activeConversationId: id })
    },

    addMessage(cid: string, msg: ChatMessage) {
      set((s) => ({
        conversations: s.conversations.map((conv) => {
          if (conv.id !== cid) return conv
          const t = conv.messages.length === 0 && msg.role === "user"
            ? msg.content.slice(0, 50) + (msg.content.length > 50 ? "..." : "")
            : conv.title
          return { ...conv, title: t, messages: [...conv.messages, msg] }
        }),
      }))
    },

    updateMessage(cid: string, mid: string, u: Partial<ChatMessage>) {
      set((s) => ({
        conversations: s.conversations.map((conv) => {
          if (conv.id !== cid) return conv
          return { ...conv, messages: conv.messages.map((m) => m.id === mid ? { ...m, ...u } : m) }
        }),
      }))
    },

    deleteConversation(id: string) {
      const uid = getUserId()
      set((s) => ({
        conversations: s.conversations.filter((c) => !(c.id === id && c.user_id === uid)),
        activeConversationId: s.activeConversationId === id ? null : s.activeConversationId,
      }))
    },

    clearActive() {
      set({ activeConversationId: null })
    },

    clearAll() {
      const uid = getUserId()
      set((s) => ({
        conversations: s.conversations.filter((c) => c.user_id !== uid),
        activeConversationId: null,
      }))
    },
  }
  return obj
}, { name: "quillflow-chats", partialize: (s) => ({ conversations: s.conversations.slice(0, 100), activeConversationId: s.activeConversationId }) }))