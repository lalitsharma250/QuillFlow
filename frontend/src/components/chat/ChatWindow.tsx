import { useRef, useEffect, useState } from "react"
import { chatApi } from "@/api/chat"
import { useChatStore } from "@/stores/chatStore"
import { useThemeStore } from "@/stores/themeStore"
import type { ChatMessage, StreamEvent, Source, TokenUsage } from "@/lib/types"
import MessageBubble from "./MessageBubble"
import StreamingMessage from "./StreamingMessage"
import ChatInput from "./ChatInput"

export default function ChatWindow() {
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamContent, setStreamContent] = useState("")
  const [statusMessages, setStatusMessages] = useState<string[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const isDark = useThemeStore((s) => s.theme === "dark")

  const contentRef = useRef("")
  const sourcesRef = useRef<Source[]>([])
  const usageRef = useRef<TokenUsage | null>(null)
  const queryTypeRef = useRef("simple")

  const store = useChatStore()
  const conversation = store.activeConversation()
  const messages = conversation ? conversation.messages : []

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, streamContent])

  const handleSend = (query: string) => {
    let convId = store.activeConversationId
    if (!convId) {
      convId = store.createConversation()
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: query,
      created_at: new Date().toISOString(),
    }
    store.addMessage(convId, userMessage)

    const currentConv = useChatStore.getState().conversations.find((c) => c.id === convId)
    const history = (currentConv ? currentConv.messages : [])
      .slice(-20)
      .map((m) => ({ role: m.role, content: m.content }))

    setIsStreaming(true)
    setStreamContent("")
    setStatusMessages([])
    contentRef.current = ""
    sourcesRef.current = []
    usageRef.current = null
    queryTypeRef.current = "simple"

    const assistantId = crypto.randomUUID()
    const savedConvId = convId

    abortRef.current = chatApi.streamQuery(
      query,
      history,
      (event: StreamEvent) => {
        if (event.type === "stream_start") {
          setStatusMessages(["Starting..."])
        } else if (event.type === "status_update") {
          if (event.message) setStatusMessages((prev) => [...prev, event.message!])
          if (event.query_type) queryTypeRef.current = event.query_type
        } else if (event.type === "content_delta") {
          if (event.content) {
            contentRef.current = contentRef.current + event.content
            setStreamContent(contentRef.current)
          }
        } else if (event.type === "section_start") {
          if (event.heading) setStatusMessages((prev) => [...prev, "Writing: " + event.heading])
        } else if (event.type === "stream_end") {
          if (event.sources) sourcesRef.current = event.sources
          if (event.usage) usageRef.current = event.usage
        } else if (event.type === "error") {
          contentRef.current = event.error_detail || "An error occurred"
          setStreamContent(contentRef.current)
        }
      },
      (error: string) => {
        contentRef.current = "Error: " + error
        setStreamContent(contentRef.current)
        setIsStreaming(false)
      },
      () => {
        const assistantMessage: ChatMessage = {
          id: assistantId,
          role: "assistant",
          content: contentRef.current,
          sources: sourcesRef.current,
          usage: usageRef.current || undefined,
          query_type: queryTypeRef.current,
          cached: false,
          created_at: new Date().toISOString(),
        }
        store.addMessage(savedConvId, assistantMessage)
        setIsStreaming(false)
        setStreamContent("")
        setStatusMessages([])
      }
    )
  }

  useEffect(() => {
    return () => { abortRef.current?.abort() }
  }, [])

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-4xl mx-auto space-y-6">

          {messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center">
              <div className={isDark ? "w-16 h-16 bg-blue-600/20 rounded-2xl flex items-center justify-center mb-4" : "w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mb-4"}>
                <span className="text-3xl">✨</span>
              </div>
              <h2 className={isDark ? "text-xl font-semibold text-white mb-2" : "text-xl font-semibold text-gray-900 mb-2"}>
                What would you like to know?
              </h2>
              <p className={isDark ? "text-slate-400 text-sm max-w-md" : "text-gray-500 text-sm max-w-md"}>
                Ask questions about your documents. I'll search your knowledge base and provide grounded answers with citations.
              </p>
              <div className="flex flex-wrap gap-2 mt-6 justify-center">
                {["What is RAG?", "How does self-attention work?", "Explain chain of thought prompting", "What are embedding models?"].map((s) => (
                  <button
                    key={s}
                    onClick={() => handleSend(s)}
                    className={isDark
                      ? "px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-full text-sm text-slate-300 hover:bg-slate-700 hover:text-white transition-colors"
                      : "px-3 py-1.5 bg-white border border-gray-200 rounded-full text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-900 shadow-sm transition-colors"
                    }
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}

          {isStreaming && (
            <StreamingMessage content={streamContent} statusMessages={statusMessages} isStreaming={true} />
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      <ChatInput onSend={handleSend} disabled={isStreaming} placeholder={isStreaming ? "Generating response..." : undefined} />
    </div>
  )
}