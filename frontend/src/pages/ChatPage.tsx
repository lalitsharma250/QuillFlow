import ChatWindow from '@/components/chat/ChatWindow'
import { useAuthStore } from '@/stores/authStore'
import { useChatStore } from '@/stores/chatStore'
import { useThemeStore } from '@/stores/themeStore'

export default function ChatPage() {
  const user = useAuthStore((s) => s.user)
  const conversations = useChatStore((s) => s.conversations)
  const activeConversationId = useChatStore((s) => s.activeConversationId)
  const messages = useChatStore((s) => s.messages)
  const isDark = useThemeStore((s) => s.theme === 'dark')

  const activeConv = conversations.find((c) => c.id === activeConversationId)
  const title = activeConv?.title || 'New Chat'
  const messageCount = messages.length

  return (
    <div className="flex flex-col h-full">
      <div className={`flex items-center justify-between px-6 py-3 border-b ${
        isDark ? 'border-slate-800 bg-slate-900/50' : 'border-gray-200 bg-white'
      }`}>
        <div className="flex items-center gap-3">
          <h1 className={`text-sm font-medium ${isDark ? 'text-white' : 'text-gray-900'}`}>
            {title}
          </h1>
          {messageCount > 0 && (
            <span className={`text-xs ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
              {messageCount} message{messageCount !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        <div className={`flex items-center gap-2 text-xs ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
          <span>Ctrl+K for new chat</span>
          <span>•</span>
          <span>{user?.org_name}</span>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <ChatWindow />
      </div>
    </div>
  )
}