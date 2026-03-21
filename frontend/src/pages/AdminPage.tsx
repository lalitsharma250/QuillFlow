import { useState } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { ROLES } from '@/lib/constants'
import SystemStats from '@/components/admin/SystemStats'
import UserManagement from '@/components/admin/UserManagement'
import InviteManager from '@/components/admin/InviteManager'
import AuditLog from '@/components/admin/AuditLog'
import OrgManagement from '@/components/admin/OrgManagement'

type AdminTab = 'overview' | 'users' | 'invites' | 'audit' | 'organizations'

export default function AdminPage() {
  const [tab, setTab] = useState<AdminTab>('overview')
  const user = useAuthStore((s) => s.user)
  const isDark = useThemeStore((s) => s.theme === 'dark')

  if (user?.role !== ROLES.ADMIN) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <span className="text-4xl mb-3 block">🔒</span>
          <p className={`font-medium ${isDark ? 'text-white' : 'text-gray-900'}`}>Admin access required</p>
          <p className={`text-sm mt-1 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
            Contact your organization admin
          </p>
        </div>
      </div>
    )
  }

  const isSuperAdmin = user?.is_superadmin === true

  const tabs: { id: AdminTab; label: string; icon: string; superOnly?: boolean }[] = [
    { id: 'overview', label: 'Overview', icon: '📊' },
    { id: 'users', label: 'Users', icon: '👥' },
    { id: 'invites', label: 'Invites', icon: '🔗' },
    { id: 'audit', label: 'Audit Log', icon: '📋' },
    ...(isSuperAdmin ? [{ id: 'organizations' as AdminTab, label: 'Organizations', icon: '🏢', superOnly: true }] : []),
  ]

  return (
    <div className="h-full flex flex-col">
      <div className={`px-6 py-4 border-b ${
        isDark ? 'border-slate-800' : 'border-gray-200 bg-white'
      }`}>
        <div className="flex items-center gap-3">
          <h1 className={`text-xl font-semibold ${isDark ? 'text-white' : 'text-gray-900'}`}>
            Admin Dashboard
          </h1>
          {isSuperAdmin && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 font-medium">
              Super Admin
            </span>
          )}
        </div>
        <p className={`text-sm mt-0.5 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
          {user?.org_name}
        </p>
      </div>

      <div className={`px-6 border-b ${isDark ? 'border-slate-800' : 'border-gray-200 bg-white'}`}>
        <div className="flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                tab === t.id
                  ? 'border-blue-500 text-blue-600'
                  : isDark
                    ? 'border-transparent text-slate-400 hover:text-white'
                    : 'border-transparent text-gray-500 hover:text-gray-900'
              }`}
            >
              <span className="mr-1.5">{t.icon}</span>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {tab === 'overview' && <SystemStats />}
        {tab === 'users' && <UserManagement />}
        {tab === 'invites' && <InviteManager />}
        {tab === 'audit' && <AuditLog />}
        {tab === 'organizations' && isSuperAdmin && <OrgManagement />}
      </div>
    </div>
  )
}