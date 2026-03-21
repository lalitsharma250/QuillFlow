import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { useChatStore } from '@/stores/chatStore'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { useThemeStore } from '@/stores/themeStore'
import { ROUTES, ROLES } from '@/lib/constants'
import { truncate } from '@/lib/utils'

export default function MainLayout() {
  useKeyboardShortcuts()
  const location = useLocation()
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const { theme, toggleTheme } = useThemeStore()
  const { activeConversationId, setActiveConversation, createConversation, deleteConversation, userConversations,} = useChatStore()
  const conversations = userConversations()

  const isDark = theme === 'dark'
  const isActive = (path: string) => location.pathname === path

  const handleNewChat = () => {
    createConversation()
    navigate(ROUTES.CHAT)
  }

  const handleSelectChat = (id: string) => {
    setActiveConversation(id)
    navigate(ROUTES.CHAT)
  }

  const handleLogout = () => {
    useChatStore.getState().clearActive()
    logout()
    navigate(ROUTES.LOGIN, { replace: true })
  }

  // Theme-aware class helpers
  const sidebarBg = isDark ? 'bg-slate-950' : 'bg-white'
  const borderColor = isDark ? 'border-slate-700' : 'border-gray-200'
  const textPrimary = isDark ? 'text-white' : 'text-gray-900'
  const textSecondary = isDark ? 'text-slate-300' : 'text-gray-600'
  const textMuted = isDark ? 'text-slate-500' : 'text-gray-400'
  const hoverBg = isDark ? 'hover:bg-slate-800' : 'hover:bg-gray-100'
  const activeBg = isDark ? 'bg-slate-800 border-slate-700' : 'bg-blue-50 border-blue-200'
  const activeText = isDark ? 'text-white' : 'text-blue-700'
  const mainBg = isDark ? 'bg-slate-900' : 'bg-gray-50'
  const navActiveBg = 'bg-blue-600/20 text-blue-500 border border-blue-600/30'
  const navInactive = isDark
    ? 'text-slate-300 hover:bg-slate-800 hover:text-white'
    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'

  return (
    <div className={`h-screen flex ${mainBg}`}>
      {/* Sidebar */}
      <aside className={`w-64 flex flex-col border-r ${sidebarBg} ${borderColor}`}>

        {/* Logo */}
        <div className={`p-4 border-b ${borderColor}`}>
          <Link to={ROUTES.CHAT} className="flex items-center gap-2">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold">Q</span>
            </div>
            <span className={`font-semibold text-lg ${textPrimary}`}>QuillFlow</span>
          </Link>
        </div>

        {/* New Chat Button */}
        <div className="p-3">
          <button
            onClick={handleNewChat}
            className="w-full px-3 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2 justify-center"
          >
            <span className="text-lg">+</span>
            <span>New Chat</span>
          </button>
        </div>

        {/* Chat History */}
        <div className="flex-1 overflow-y-auto px-3 space-y-0.5">
          <p className={`text-xs uppercase tracking-wider px-3 py-2 font-medium ${textMuted}`}>
            Recent Chats
          </p>
          {conversations.length === 0 ? (
            <p className={`text-sm px-3 py-2 ${textMuted}`}>No conversations yet</p>
          ) : (
            conversations.map((conv) => (
              <div
                key={conv.id}
                className={`group flex items-center rounded-lg transition-colors ${
                  activeConversationId === conv.id
                    ? `${activeBg} border`
                    : `${hoverBg}`
                }`}
              >
                <button
                  onClick={() => handleSelectChat(conv.id)}
                  className={`flex-1 text-left px-3 py-2 text-sm truncate ${
                    activeConversationId === conv.id
                      ? `${activeText} font-medium`
                      : `${textSecondary} ${isDark ? 'hover:text-white' : 'hover:text-gray-900'}`
                  }`}
                >
                  💬 {truncate(conv.title, 22)}
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    if (confirm('Delete this conversation?')) {
                      deleteConversation(conv.id)
                    }
                  }}
                  className="hidden group-hover:block px-2 py-1 text-slate-500 hover:text-red-400 text-xs"
                  title="Delete"
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>

        {/* Navigation */}
        <nav className={`p-3 border-t ${borderColor} space-y-1`}>
          <Link
            to={ROUTES.CHAT}
            className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              isActive(ROUTES.CHAT) ? navActiveBg : navInactive
            }`}
          >
            <span>💬</span>
            <span>Chat</span>
          </Link>

          <Link
            to={ROUTES.DOCUMENTS}
            className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              isActive(ROUTES.DOCUMENTS) ? navActiveBg : navInactive
            }`}
          >
            <span>📄</span>
            <span>Documents</span>
          </Link>

          {user?.role === ROLES.ADMIN && (
            <Link
              to={ROUTES.ADMIN}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                isActive(ROUTES.ADMIN) ? navActiveBg : navInactive
              }`}
            >
              <span>⚙️</span>
              <span>Admin</span>
            </Link>
          )}

          {/* Theme Toggle */}
          <button
            onClick={toggleTheme}
            className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors w-full ${navInactive}`}
          >
            <span>{isDark ? '☀️' : '🌙'}</span>
            <span>{isDark ? 'Light Mode' : 'Dark Mode'}</span>
          </button>
        </nav>

        {/* User Info */}
        <div className={`p-3 border-t ${borderColor}`}>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center flex-shrink-0">
              <span className="text-sm text-white font-bold">
                {user?.name?.charAt(0)?.toUpperCase() || '?'}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <p className={`text-sm font-medium truncate ${textPrimary}`}>{user?.name}</p>
              <p className={`text-xs truncate ${textMuted}`}>
                {user?.org_name} • <span className="capitalize">{user?.role}</span>
              </p>
            </div>
            <button
              onClick={handleLogout}
              className={`p-1.5 rounded-lg transition-colors ${
                isDark
                  ? 'text-slate-400 hover:text-red-400 hover:bg-slate-800'
                  : 'text-gray-400 hover:text-red-500 hover:bg-gray-100'
              }`}
              title="Logout"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className={`flex-1 flex flex-col overflow-hidden ${mainBg}`}>
        <Outlet />
      </main>
    </div>
  )
}