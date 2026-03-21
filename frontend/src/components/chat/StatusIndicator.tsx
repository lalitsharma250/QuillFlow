
interface StatusIndicatorProps {
  messages: string[]
}

export default function StatusIndicator({ messages }: StatusIndicatorProps) {
  if (messages.length === 0) return null

  const latestMessage = messages[messages.length - 1]

  return (
    <div className="flex items-center gap-2 px-4 py-2 text-sm text-slate-400">
      <div className="flex gap-1">
        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
      </div>
      <span>{latestMessage}</span>
    </div>
  )
}