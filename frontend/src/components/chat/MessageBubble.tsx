import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '@/lib/types'
import SourceCard from './SourceCard'
import { formatDate, formatCost } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'

interface MessageBubbleProps {
  message: ChatMessage
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user'
  const isDark = useThemeStore((s) => s.theme === 'dark')

  return (
    <div className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {/* Assistant Avatar */}
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center flex-shrink-0 mt-1">
          <span className="text-white text-sm font-bold">Q</span>
        </div>
      )}

      {/* Message Content */}
      <div className={`max-w-[80%] ${isUser ? 'order-first' : ''}`}>
        <div className={`rounded-2xl px-4 py-3 ${
          isUser
            ? 'bg-blue-600 text-white rounded-br-md'
            : isDark
              ? 'bg-slate-800 border border-slate-700 text-slate-200 rounded-bl-md'
              : 'bg-white border border-gray-200 text-gray-800 rounded-bl-md shadow-sm'
        }`}>
          {isUser ? (
            <p className="text-sm whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className={`text-sm ${isDark ? 'markdown-content' : 'markdown-content-light'}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Sources */}
        {!isUser && message.sources && (
  <SourceCard sources={message.sources} answerText={message.content} />
)}
        {/* Metadata */}
        {!isUser && (message.usage || message.cached !== undefined) && (
          <div className="flex items-center gap-3 mt-1.5 px-1 flex-wrap">
            <span className={`text-xs ${isDark ? 'text-slate-600' : 'text-gray-400'}`}>
              {formatDate(message.created_at)}
            </span>
            {message.cached && (
              <span className="text-xs text-amber-500">⚡ cached</span>
            )}
            {message.query_type && (
              <span className={`text-xs px-1.5 py-0.5 rounded ${
                message.query_type === 'complex'
                  ? 'bg-purple-100 text-purple-600'
                  : isDark
                    ? 'bg-slate-700 text-slate-400'
                    : 'bg-gray-100 text-gray-500'
              }`}>
                {message.query_type}
              </span>
            )}
          </div>
        )}
      </div>

      {/* User Avatar */}
      {isUser && (
        <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-1 ${
          isDark ? 'bg-slate-600' : 'bg-gray-400'
        }`}>
          <span className="text-white text-sm">👤</span>
        </div>
      )}
    </div>
  )
}