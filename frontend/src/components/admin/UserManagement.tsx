import { useState, useEffect } from 'react'
import { adminApi } from '@/api/admin'
import { toast } from '@/components/ui/toaster'
import { formatDate } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import { useAuthStore } from '@/stores/authStore'
import type { OrgUser } from '@/lib/types'

export default function UserManagement() {
  const [users, setUsers] = useState<OrgUser[]>([])
  const [loading, setLoading] = useState(true)
  const [showInactive, setShowInactive] = useState(true)
  const isDark = useThemeStore((s) => s.theme === 'dark')
  const currentUser = useAuthStore((s) => s.user)
  const isSuperAdmin = currentUser?.is_superadmin === true

  const fetchUsers = async () => {
    try {
      const data = await adminApi.listUsers(showInactive)
      setUsers(data.users)
    } catch {
      toast('Failed to load users', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchUsers() }, [showInactive])

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      await adminApi.updateRole(userId, newRole)
      toast('Role updated', 'success')
      fetchUsers()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Failed to update role', 'error')
    }
  }

  const handleDeactivate = async (user: OrgUser) => {
    if (!confirm(`Deactivate ${user.email}? They will lose access immediately.`)) return
    try {
      await adminApi.deactivateUser(user.user_id)
      toast(`${user.email} deactivated`, 'success')
      fetchUsers()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Failed to deactivate', 'error')
    }
  }

  const handleReactivate = async (user: OrgUser) => {
    if (!confirm(`Reactivate ${user.email}?`)) return
    try {
      await adminApi.reactivateUser(user.user_id)
      toast(`${user.email} reactivated`, 'success')
      fetchUsers()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Failed to reactivate', 'error')
    }
  }

  // Permission helpers
  const isCurrentUser = (user: OrgUser) => user.user_id === currentUser?.user_id
  const canChangeRole = (user: OrgUser) => {
    if (isCurrentUser(user)) return false
    if (!user.is_active) return false
    if (user.role === 'admin' && !isSuperAdmin) return false
    return true
  }
  const canDeactivate = (user: OrgUser) => {
    if (isCurrentUser(user)) return false
    if (user.role === 'admin' && !isSuperAdmin) return false
    return true
  }
  const getRoleOptions = (user: OrgUser) => {
    if (isSuperAdmin) return ['admin', 'editor', 'viewer']
    // Non-superadmin can only set editor or viewer
    return ['editor', 'viewer']
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
        <span className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>{users.length} users</span>
        <label className={`flex items-center gap-2 text-sm cursor-pointer ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
            className="rounded"
          />
          Show inactive
        </label>
      </div>

      <div className={`border rounded-lg overflow-hidden ${isDark ? 'border-slate-700' : 'border-gray-200'}`}>
        <table className="w-full">
          <thead>
            <tr className={isDark ? 'bg-slate-800/50' : 'bg-gray-50'}>
              <th className={`text-left px-4 py-3 text-xs font-medium uppercase ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>User</th>
              <th className={`text-left px-4 py-3 text-xs font-medium uppercase ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>Role</th>
              <th className={`text-left px-4 py-3 text-xs font-medium uppercase ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>Status</th>
              <th className={`text-left px-4 py-3 text-xs font-medium uppercase ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>Joined</th>
              <th className={`text-right px-4 py-3 text-xs font-medium uppercase ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>Actions</th>
            </tr>
          </thead>
          <tbody className={`divide-y ${isDark ? 'divide-slate-800' : 'divide-gray-100'}`}>
            {users.map((user) => (
              <tr
                key={user.user_id}
                className={`transition-colors ${!user.is_active ? 'opacity-50' : ''} ${
                  isDark ? 'hover:bg-slate-800/30' : 'hover:bg-gray-50'
                }`}
              >
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div>
                      <p className={`text-sm font-medium ${isDark ? 'text-white' : 'text-gray-900'}`}>
                        {user.name}
                        {isCurrentUser(user) && (
                          <span className={`ml-2 text-xs ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>(You)</span>
                        )}
                      </p>
                      <p className={`text-xs ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>{user.email}</p>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3">
                  {canChangeRole(user) ? (
                    <select
                      value={user.role}
                      onChange={(e) => handleRoleChange(user.user_id, e.target.value)}
                      className={`border rounded px-2 py-1 text-xs ${
                        isDark
                          ? 'bg-slate-900 border-slate-700 text-white'
                          : 'bg-white border-gray-300 text-gray-900'
                      }`}
                    >
                      {getRoleOptions(user).map((role) => (
                        <option key={role} value={role}>{role}</option>
                      ))}
                    </select>
                  ) : (
                    <span className={`text-xs px-2 py-1 rounded ${
                      user.role === 'admin' ? 'bg-blue-100 text-blue-700' :
                      user.role === 'editor' ? 'bg-green-100 text-green-700' :
                      isDark ? 'bg-slate-700 text-slate-300' : 'bg-gray-100 text-gray-600'
                    }`}>
                      {user.role}
                      {user.role === 'admin' && !isSuperAdmin && (
                        <span className="ml-1" title="Only super admins can change admin roles">🔒</span>
                      )}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    user.is_active ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                  }`}>
                    {user.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                    {formatDate(user.created_at)}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  {isCurrentUser(user) ? (
                    <span className={`text-xs ${isDark ? 'text-slate-600' : 'text-gray-400'}`}>—</span>
                  ) : user.is_active ? (
                    canDeactivate(user) ? (
                      <button
                        onClick={() => handleDeactivate(user)}
                        className="text-xs text-gray-400 hover:text-red-500 transition-colors"
                      >
                        Deactivate
                      </button>
                    ) : (
                      <span className={`text-xs ${isDark ? 'text-slate-600' : 'text-gray-400'}`} title="Only super admins can deactivate admins">
                        🔒
                      </span>
                    )
                  ) : (
                    <button
                      onClick={() => handleReactivate(user)}
                      className="text-xs text-gray-400 hover:text-green-500 transition-colors"
                    >
                      Reactivate
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Permission Legend */}
      {!isSuperAdmin && (
        <div className={`mt-4 text-xs flex items-center gap-1 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
          <span>🔒</span>
          <span>Admin roles can only be managed by super admins</span>
        </div>
      )}
    </div>
  )
}