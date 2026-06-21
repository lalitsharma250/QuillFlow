import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '@/lib/types'
import SourceCard from './SourceCard'
import { formatDate} from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'

interface MessageBubbleProps {
  message: ChatMessage
}

/**
 * Scroll to and briefly highlight the source row a citation [n] points to.
 * No-ops gracefully if the source isn't rendered (e.g. uncited-and-collapsed,
 * or out of range).
 */
function focusSource(messageId: string, citationNumber: number) {
  const el = document.getElementById(`src-${messageId}-${citationNumber - 1}`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('citation-flash')
  window.setTimeout(() => el.classList.remove('citation-flash'), 1200)
}

/**
 * Replace [n] tokens inside a markdown text node with clickable citation chips.
 * Operates only on string children so it never breaks nested markdown nodes.
 */
function renderWithCitations(children: ReactNode, messageId: string): ReactNode {
  if (typeof children !== 'string') {
    if (Array.isArray(children)) {
      return children.map((c, i) => <span key={i}>{renderWithCitations(c, messageId)}</span>)
    }
    return children
  }

  const parts: ReactNode[] = []
  const pattern = /\[(\d+)\]/g
  let last = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = pattern.exec(children)) !== null) {
    if (match.index > last) parts.push(children.slice(last, match.index))
    const n = parseInt(match[1], 10)
    parts.push(
      <button
        key={`cite-${key++}`}
        type="button"
        onClick={() => focusSource(messageId, n)}
        className="citation-ref"
        title={`Jump to source ${n}`}
      >
        [{n}]
      </button>
    )
    last = match.index + match[0].length
  }
  if (last < children.length) parts.push(children.slice(last))
  return parts
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
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p: ({ children }) => <p>{renderWithCitations(children, message.id)}</p>,
                  li: ({ children }) => <li>{renderWithCitations(children, message.id)}</li>,
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Sources */}
        {!isUser && message.sources && message.sources.length > 0 && (
          <SourceCard
            sources={message.sources}
            answerText={message.content}
            messageId={message.id}
          />
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