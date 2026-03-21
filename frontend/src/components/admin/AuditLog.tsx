import { useState, useEffect } from 'react'
import { adminApi } from '@/api/admin'
import { toast } from '@/components/ui/toaster'
import { formatDate } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import type { AuditEntry } from '@/lib/types'

export default function AuditLog() {
  const [logs, setLogs] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [actionFilter, setActionFilter] = useState('')
  const isDark = useThemeStore((s) => s.theme === 'dark')

  const fetchLogs = async () => {
    setLoading(true)
    try {
      const data = await adminApi.getAuditLogs({ action: actionFilter || undefined, limit: 100 })
      setLogs(data.logs)
    } catch {
      toast('Failed to load audit logs', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchLogs() }, [actionFilter])

  const actionColors: Record<string, string> = {
    auth_success: 'text-green-600',
    auth_failure: 'text-red-500',
    user_signup: 'text-blue-600',
    user_created: 'text-blue-600',
    user_deactivated: 'text-red-500',
    user_reactivated: 'text-green-600',
    user_role_changed: 'text-amber-600',
    query: isDark ? 'text-slate-300' : 'text-gray-700',
    ingest_single: 'text-emerald-600',
    ingest_bulk: 'text-emerald-600',
    invite_code_created: 'text-purple-600',
    invite_code_revoked: 'text-red-500',
    admin_clear_cache: 'text-orange-500',
    admin_cleanup_stale_documents: 'text-orange-500',
    admin_delete_document: 'text-red-500',
  }

  const actionIcons: Record<string, string> = {
    auth_success: '🔓', auth_failure: '🔒', user_signup: '👤',
    user_created: '👤', user_deactivated: '🚫', user_reactivated: '✅',
    user_role_changed: '🔄', query: '💬', ingest_single: '📄',
    ingest_bulk: '📦', invite_code_created: '🔗', invite_code_revoked: '✂️',
    admin_clear_cache: '🗑️', admin_cleanup_stale_documents: '🧹',
    admin_delete_document: '🗑️',
  }

  if (loading) {
    return (
      <div className="p-6 space-y-2">
        {[...Array(8)].map((_, i) => (
          <div key={i} className={`h-10 rounded-lg animate-pulse ${isDark ? 'bg-slate-800/50' : 'bg-gray-100'}`} />
        ))}
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-4">
        <select
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          className={`px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${
            isDark
              ? 'bg-slate-800 border-slate-700 text-white'
              : 'bg-white border-gray-300 text-gray-900'
          }`}
        >
          <option value="">All Actions</option>
          <option value="auth_success">Login Success</option>
          <option value="auth_failure">Login Failure</option>
          <option value="user_signup">Signups</option>
          <option value="query">Queries</option>
          <option value="ingest_single">Single Ingest</option>
          <option value="ingest_bulk">Bulk Ingest</option>
          <option value="user_role_changed">Role Changes</option>
          <option value="invite_code_created">Invite Created</option>
        </select>

        <span className={`text-sm ${isDark ? 'text-slate-500' : 'text-gray-500'}`}>{logs.length} entries</span>

        <button
          onClick={fetchLogs}
          className={`ml-auto px-3 py-2 text-sm rounded-lg transition-colors ${
            isDark ? 'bg-slate-800 hover:bg-slate-700 text-white' : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
          }`}
        >
          🔄 Refresh
        </button>
      </div>

      {logs.length === 0 ? (
        <div className="text-center py-8">
          <span className="text-3xl mb-2 block">📋</span>
          <p className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>No audit entries found</p>
        </div>
      ) : (
        <div className="space-y-1">
          {logs.map((entry, i) => (
            <div
              key={i}
              className={`flex items-start gap-3 px-4 py-2.5 rounded-lg transition-colors ${
                isDark ? 'hover:bg-slate-800/50' : 'hover:bg-gray-50'
              }`}
            >
              <span className="text-sm mt-0.5">{actionIcons[entry.action] || '📌'}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-sm font-medium ${actionColors[entry.action] || (isDark ? 'text-slate-300' : 'text-gray-700')}`}>
                    {entry.action.replace(/_/g, ' ')}
                  </span>
                  {entry.detail && Object.keys(entry.detail).length > 0 && (
                    <span className={`text-xs truncate max-w-md ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
                      {Object.entries(entry.detail)
                        .filter(([k]) => k !== 'method')
                        .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
                        .join(' • ')}
                    </span>
                  )}
                </div>
              </div>
              <span className={`text-xs flex-shrink-0 ${isDark ? 'text-slate-600' : 'text-gray-400'}`}>
                {formatDate(entry.created_at)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}