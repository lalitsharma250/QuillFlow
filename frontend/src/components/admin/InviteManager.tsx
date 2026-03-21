import { useState, useEffect } from 'react'
import { adminApi } from '@/api/admin'
import { toast } from '@/components/ui/toaster'
import { formatExpiryDate } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import type { InviteCode } from '@/lib/types'

export default function InviteManager() {
  const [invites, setInvites] = useState<InviteCode[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [role, setRole] = useState('viewer')
  const [maxUses, setMaxUses] = useState(10)
  const [expiresDays, setExpiresDays] = useState(7)
  const [creating, setCreating] = useState(false)
  const isDark = useThemeStore((s) => s.theme === 'dark')

  const fetchInvites = async () => {
    try {
      const data = await adminApi.listInvites(true)
      setInvites(data)
    } catch {
      toast('Failed to load invites', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchInvites() }, [])

  const handleCreate = async () => {
    setCreating(true)
    try {
      const invite = await adminApi.createInvite({
        role,
        max_uses: maxUses,
        expires_in_days: expiresDays,
      })
      toast(`Invite code created: ${invite.code}`, 'success')
      setShowCreate(false)
      fetchInvites()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Failed to create invite', 'error')
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async (code: string) => {
    if (!confirm(`Revoke invite code ${code}?`)) return
    try {
      await adminApi.revokeInvite(code)
      toast('Invite revoked', 'success')
      fetchInvites()
    } catch {
      toast('Failed to revoke', 'error')
    }
  }

  const copyCode = (code: string) => {
    navigator.clipboard.writeText(code)
    toast('Code copied to clipboard', 'success')
  }

  const isExpired = (invite: InviteCode) => {
    return new Date(invite.expires_at) < new Date()
  }

  const isUsedUp = (invite: InviteCode) => {
    return invite.times_used >= invite.max_uses
  }

  if (loading) {
    return (
      <div className="p-6 space-y-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className={`h-14 rounded-lg animate-pulse ${isDark ? 'bg-slate-800/50' : 'bg-gray-100'}`} />
        ))}
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <span className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
          {invites.length} invite codes
        </span>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
        >
          + Generate Invite Code
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className={`border rounded-xl p-4 mb-4 ${
          isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-gray-200 shadow-sm'
        }`}>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className={`block text-xs mb-1 font-medium ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                Role
              </label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className={`w-full border rounded-lg px-3 py-2 text-sm ${
                  isDark
                    ? 'bg-slate-900 border-slate-600 text-white'
                    : 'bg-gray-50 border-gray-300 text-gray-900'
                }`}
              >
                <option value="viewer">Viewer</option>
                <option value="editor">Editor</option>
              </select>
            </div>
            <div>
              <label className={`block text-xs mb-1 font-medium ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                Max Uses
              </label>
              <input
                type="number"
                value={maxUses}
                onChange={(e) => setMaxUses(parseInt(e.target.value) || 1)}
                min={1}
                max={1000}
                className={`w-full border rounded-lg px-3 py-2 text-sm ${
                  isDark
                    ? 'bg-slate-900 border-slate-600 text-white'
                    : 'bg-gray-50 border-gray-300 text-gray-900'
                }`}
              />
            </div>
            <div>
              <label className={`block text-xs mb-1 font-medium ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                Expires In (days)
              </label>
              <input
                type="number"
                value={expiresDays}
                onChange={(e) => setExpiresDays(parseInt(e.target.value) || 1)}
                min={1}
                max={90}
                className={`w-full border rounded-lg px-3 py-2 text-sm ${
                  isDark
                    ? 'bg-slate-900 border-slate-600 text-white'
                    : 'bg-gray-50 border-gray-300 text-gray-900'
                }`}
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4">
            <button
              onClick={() => setShowCreate(false)}
              className={`px-3 py-1.5 text-sm rounded-lg ${
                isDark ? 'bg-slate-700 text-white' : 'bg-gray-100 text-gray-700'
              }`}
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white text-sm rounded-lg"
            >
              {creating ? 'Creating...' : 'Generate'}
            </button>
          </div>
        </div>
      )}

      {/* Invite List */}
      <div className="space-y-2">
        {invites.length === 0 ? (
          <div className="text-center py-8">
            <span className="text-3xl mb-2 block">🔗</span>
            <p className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>No invite codes yet</p>
          </div>
        ) : (
          invites.map((invite) => {
            const expired = isExpired(invite)
            const usedUp = isUsedUp(invite)
            const inactive = !invite.is_active || expired || usedUp

            return (
              <div
                key={invite.code}
                className={`flex items-center justify-between px-4 py-3 rounded-lg border transition-colors ${
                  inactive
                    ? isDark
                      ? 'bg-slate-800/30 border-slate-800 opacity-50'
                      : 'bg-gray-50 border-gray-200 opacity-50'
                    : isDark
                      ? 'bg-slate-800 border-slate-700'
                      : 'bg-white border-gray-200 shadow-sm'
                }`}
              >
                <div className="flex items-center gap-4 flex-wrap">
                  <button
                    onClick={() => copyCode(invite.code)}
                    className="font-mono text-sm text-blue-500 hover:text-blue-400 transition-colors"
                    title="Click to copy"
                  >
                    {invite.code}
                  </button>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    invite.role === 'editor'
                      ? 'bg-blue-100 text-blue-700'
                      : isDark
                        ? 'bg-slate-700 text-slate-300'
                        : 'bg-gray-100 text-gray-600'
                  }`}>
                    {invite.role}
                  </span>
                  <span className={`text-xs ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                    {invite.times_used}/{invite.max_uses} used
                  </span>
                  <span className={`text-xs font-medium ${
                    expired
                      ? 'text-red-500'
                      : isDark ? 'text-slate-400' : 'text-gray-500'
                  }`}>
                    {formatExpiryDate(invite.expires_at)}
                  </span>
                  {!invite.is_active && (
                    <span className="text-xs text-red-500 font-medium">Revoked</span>
                  )}
                  {usedUp && invite.is_active && (
                    <span className="text-xs text-amber-500 font-medium">All used</span>
                  )}
                </div>
                {invite.is_active && !expired && !usedUp && (
                  <button
                    onClick={() => handleRevoke(invite.code)}
                    className="text-xs text-gray-400 hover:text-red-500 transition-colors"
                  >
                    Revoke
                  </button>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}