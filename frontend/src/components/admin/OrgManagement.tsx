import { useState, useEffect } from 'react'
import { adminApi } from '@/api/admin'
import { toast } from '@/components/ui/toaster'
import { formatDate } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'

interface Org {
  org_id: string
  name: string
  is_active: boolean
  user_count: number
  document_count: number
  created_at: string
}

interface OrgUser {
  user_id: string
  email: string
  name: string
  role: string
  is_active: boolean
  is_superadmin: boolean
  created_at: string
}

export default function OrgManagement() {
  const [orgs, setOrgs] = useState<Org[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null)
  const [orgUsers, setOrgUsers] = useState<OrgUser[]>([])
  const [loadingUsers, setLoadingUsers] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const isDark = useThemeStore((s) => s.theme === 'dark')

  // Create form state
  const [newOrgName, setNewOrgName] = useState('')
  const [adminName, setAdminName] = useState('')
  const [adminEmail, setAdminEmail] = useState('')
  const [adminPassword, setAdminPassword] = useState('')
  const [creating, setCreating] = useState(false)
  const [createdApiKey, setCreatedApiKey] = useState('')

  const fetchOrgs = async () => {
    try {
      const data = await adminApi.superadminListOrgs()
      setOrgs(data)
    } catch {
      toast('Failed to load organizations', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchOrgs() }, [])

  const handleSelectOrg = async (orgId: string) => {
    if (selectedOrg === orgId) {
      setSelectedOrg(null)
      setOrgUsers([])
      return
    }
    setSelectedOrg(orgId)
    setLoadingUsers(true)
    try {
      const users = await adminApi.superadminListOrgUsers(orgId)
      setOrgUsers(users)
    } catch {
      toast('Failed to load users', 'error')
    } finally {
      setLoadingUsers(false)
    }
  }

  const handleCreateOrg = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newOrgName || !adminName || !adminEmail || !adminPassword) {
      toast('All fields are required', 'error')
      return
    }
    if (adminPassword.length < 8) {
      toast('Password must be at least 8 characters', 'error')
      return
    }

    setCreating(true)
    try {
      const result = await adminApi.superadminCreateOrg({
        name: newOrgName,
        admin_email: adminEmail,
        admin_name: adminName,
        admin_password: adminPassword,
      })
      toast(`Organization "${newOrgName}" created!`, 'success')
      setCreatedApiKey(result.api_key)
      setNewOrgName('')
      setAdminName('')
      setAdminEmail('')
      setAdminPassword('')
      fetchOrgs()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Failed to create organization', 'error')
    } finally {
      setCreating(false)
    }
  }

  const handleDeactivateOrg = async (org: Org) => {
    if (!confirm(`Deactivate "${org.name}"? All ${org.user_count} users will lose access.`)) return
    try {
      await adminApi.superadminDeactivateOrg(org.org_id)
      toast(`"${org.name}" deactivated`, 'success')
      fetchOrgs()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Failed', 'error')
    }
  }

  const handleReactivateOrg = async (org: Org) => {
    if (!confirm(`Reactivate "${org.name}"?`)) return
    try {
      await adminApi.superadminReactivateOrg(org.org_id)
      toast(`"${org.name}" reactivated`, 'success')
      fetchOrgs()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Failed', 'error')
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    toast('Copied to clipboard', 'success')
  }

  if (loading) {
    return (
      <div className="p-6 space-y-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className={`h-20 rounded-xl animate-pulse ${isDark ? 'bg-slate-800/50' : 'bg-gray-100'}`} />
        ))}
      </div>
    )
  }

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <span className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
          {orgs.length} organization{orgs.length !== 1 ? 's' : ''}
        </span>
        <button
          onClick={() => { setShowCreate(!showCreate); setCreatedApiKey('') }}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
        >
          + Create Organization
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className={`border rounded-xl p-5 mb-6 ${
          isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-gray-200 shadow-sm'
        }`}>
          <h3 className={`text-sm font-semibold mb-4 ${isDark ? 'text-white' : 'text-gray-900'}`}>
            Create New Organization
          </h3>

          {createdApiKey ? (
            <div className="space-y-3">
              <div className={`p-4 rounded-lg border ${isDark ? 'bg-green-900/20 border-green-800' : 'bg-green-50 border-green-200'}`}>
                <p className="text-green-600 font-medium text-sm mb-2">✅ Organization created successfully!</p>
                <p className={`text-xs mb-2 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                  Admin API key (save this — it won't be shown again):
                </p>
                <div className="flex items-center gap-2">
                  <code className={`text-xs px-2 py-1 rounded flex-1 truncate ${
                    isDark ? 'bg-slate-900 text-green-400' : 'bg-white text-green-700 border border-green-200'
                  }`}>
                    {createdApiKey}
                  </code>
                  <button
                    onClick={() => copyToClipboard(createdApiKey)}
                    className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700"
                  >
                    Copy
                  </button>
                </div>
              </div>
              <button
                onClick={() => { setShowCreate(false); setCreatedApiKey('') }}
                className={`text-sm ${isDark ? 'text-slate-400 hover:text-white' : 'text-gray-500 hover:text-gray-900'}`}
              >
                Close
              </button>
            </div>
          ) : (
            <form onSubmit={handleCreateOrg} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="col-span-2">
                  <label className={`block text-xs font-medium mb-1 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                    Organization Name
                  </label>
                  <input
                    type="text"
                    value={newOrgName}
                    onChange={(e) => setNewOrgName(e.target.value)}
                    placeholder="Acme Corporation"
                    required
                    className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                      isDark
                        ? 'bg-slate-900 border-slate-600 text-white placeholder-slate-500'
                        : 'bg-gray-50 border-gray-300 text-gray-900 placeholder-gray-400'
                    }`}
                  />
                </div>

                <div>
                  <label className={`block text-xs font-medium mb-1 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                    Admin Name
                  </label>
                  <input
                    type="text"
                    value={adminName}
                    onChange={(e) => setAdminName(e.target.value)}
                    placeholder="John Doe"
                    required
                    className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                      isDark
                        ? 'bg-slate-900 border-slate-600 text-white placeholder-slate-500'
                        : 'bg-gray-50 border-gray-300 text-gray-900 placeholder-gray-400'
                    }`}
                  />
                </div>

                <div>
                  <label className={`block text-xs font-medium mb-1 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                    Admin Email
                  </label>
                  <input
                    type="email"
                    value={adminEmail}
                    onChange={(e) => setAdminEmail(e.target.value)}
                    placeholder="admin@acme.com"
                    required
                    className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                      isDark
                        ? 'bg-slate-900 border-slate-600 text-white placeholder-slate-500'
                        : 'bg-gray-50 border-gray-300 text-gray-900 placeholder-gray-400'
                    }`}
                  />
                </div>

                <div className="col-span-2">
                  <label className={`block text-xs font-medium mb-1 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                    Admin Password <span className={isDark ? 'text-slate-600' : 'text-gray-400'}>(min 8 characters)</span>
                  </label>
                  <input
                    type="password"
                    value={adminPassword}
                    onChange={(e) => setAdminPassword(e.target.value)}
                    placeholder="••••••••"
                    required
                    minLength={8}
                    className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                      isDark
                        ? 'bg-slate-900 border-slate-600 text-white placeholder-slate-500'
                        : 'bg-gray-50 border-gray-300 text-gray-900 placeholder-gray-400'
                    }`}
                  />
                </div>
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className={`px-4 py-2 text-sm rounded-lg ${
                    isDark ? 'bg-slate-700 text-white hover:bg-slate-600' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white text-sm font-medium rounded-lg"
                >
                  {creating ? 'Creating...' : 'Create Organization'}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* Org List */}
      <div className="space-y-3">
        {orgs.map((org) => (
          <div key={org.org_id}>
            <div
              className={`border rounded-xl p-4 transition-colors cursor-pointer ${
                selectedOrg === org.org_id
                  ? isDark
                    ? 'bg-slate-800 border-blue-600'
                    : 'bg-blue-50 border-blue-300'
                  : isDark
                    ? 'bg-slate-800 border-slate-700 hover:border-slate-600'
                    : 'bg-white border-gray-200 hover:border-gray-300 shadow-sm'
              } ${!org.is_active ? 'opacity-50' : ''}`}
              onClick={() => handleSelectOrg(org.org_id)}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                    isDark ? 'bg-slate-700' : 'bg-gray-100'
                  }`}>
                    <span className="text-lg">🏢</span>
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className={`font-medium ${isDark ? 'text-white' : 'text-gray-900'}`}>
                        {org.name}
                      </h3>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        org.is_active ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                      }`}>
                        {org.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                    <p className={`text-xs mt-0.5 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                      {org.user_count} users • {org.document_count} documents • Created {formatDate(org.created_at)}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {org.is_active ? (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeactivateOrg(org) }}
                      className="text-xs text-gray-400 hover:text-red-500 transition-colors px-2 py-1"
                    >
                      Deactivate
                    </button>
                  ) : (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleReactivateOrg(org) }}
                      className="text-xs text-gray-400 hover:text-green-500 transition-colors px-2 py-1"
                    >
                      Reactivate
                    </button>
                  )}
                  <span className={`text-xs ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
                    {selectedOrg === org.org_id ? '▲' : '▼'}
                  </span>
                </div>
              </div>
            </div>

            {/* Expanded Users */}
            {selectedOrg === org.org_id && (
              <div className={`ml-6 mt-2 border-l-2 pl-4 space-y-1 ${
                isDark ? 'border-slate-700' : 'border-gray-200'
              }`}>
                {loadingUsers ? (
                  <div className={`h-10 rounded-lg animate-pulse ${isDark ? 'bg-slate-800/50' : 'bg-gray-100'}`} />
                ) : orgUsers.length === 0 ? (
                  <p className={`text-sm py-2 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>No users</p>
                ) : (
                  orgUsers.map((user) => (
                    <div
                      key={user.user_id}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg ${
                        isDark ? 'hover:bg-slate-800/50' : 'hover:bg-gray-50'
                      } ${!user.is_active ? 'opacity-50' : ''}`}
                    >
                      <div className="flex items-center gap-3">
                        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium ${
                          user.is_superadmin
                            ? 'bg-purple-100 text-purple-700'
                            : isDark ? 'bg-slate-700 text-white' : 'bg-gray-200 text-gray-700'
                        }`}>
                          {user.name.charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className={`text-sm ${isDark ? 'text-white' : 'text-gray-900'}`}>
                              {user.name}
                            </span>
                            {user.is_superadmin && (
                              <span className="text-xs px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 font-medium">
                                Super
                              </span>
                            )}
                          </div>
                          <span className={`text-xs ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
                            {user.email}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          user.role === 'admin' ? 'bg-blue-100 text-blue-700' :
                          user.role === 'editor' ? 'bg-green-100 text-green-700' :
                          isDark ? 'bg-slate-700 text-slate-300' : 'bg-gray-100 text-gray-600'
                        }`}>
                          {user.role}
                        </span>
                        <span className={`text-xs ${user.is_active ? 'text-green-600' : 'text-red-500'}`}>
                          {user.is_active ? '●' : '○'}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}