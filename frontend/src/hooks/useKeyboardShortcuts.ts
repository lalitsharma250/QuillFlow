import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChatStore } from '@/stores/chatStore'
import { ROUTES } from '@/lib/constants'

export function useKeyboardShortcuts() {
  const navigate = useNavigate()
  const createConversation = useChatStore((s) => s.createConversation)

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+K or Cmd+K — New chat
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        createConversation()
        navigate(ROUTES.CHAT)
      }

      // Ctrl+D or Cmd+D — Documents
      if ((e.ctrlKey || e.metaKey) && e.key === 'd') {
        e.preventDefault()
        navigate(ROUTES.DOCUMENTS)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [navigate, createConversation])
}