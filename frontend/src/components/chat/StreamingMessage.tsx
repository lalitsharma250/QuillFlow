import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import StatusIndicator from './StatusIndicator'
import { useThemeStore } from '@/stores/themeStore'

interface StreamingMessageProps {
  content: string
  statusMessages: string[]
  isStreaming: boolean
}

export default function StreamingMessage({ content, statusMessages, isStreaming }: StreamingMessageProps) {
  const isDark = useThemeStore((s) => s.theme === 'dark')

  return (
    <div className="flex gap-3 justify-start">
      <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center flex-shrink-0 mt-1">
        <span className="text-white text-sm font-bold">Q</span>
      </div>

      <div className="max-w-[80%]">
        <div className={`rounded-2xl rounded-bl-md px-4 py-3 ${
          isDark
            ? 'bg-slate-800 border border-slate-700 text-slate-200'
            : 'bg-white border border-gray-200 text-gray-800 shadow-sm'
        }`}>
          {content ? (
            <div className={`text-sm ${isDark ? 'markdown-content' : 'markdown-content-light'}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {content}
              </ReactMarkdown>
              {isStreaming && (
                <span className="inline-block w-2 h-4 bg-blue-500 animate-pulse ml-1" />
              )}
            </div>
          ) : (
            <StatusIndicator messages={statusMessages} />
          )}
        </div>
      </div>
    </div>
  )
}