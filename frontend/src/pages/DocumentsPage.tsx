import { useState } from 'react'
import { useThemeStore } from '@/stores/themeStore'
import { useAuthStore } from '@/stores/authStore'
import { ROLES } from '@/lib/constants'
import DocumentList from '@/components/documents/DocumentList'
import DocumentUpload from '@/components/documents/DocumentUpload'

export default function DocumentsPage() {
  const [showUpload, setShowUpload] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const isDark = useThemeStore((s) => s.theme === 'dark')
  const user = useAuthStore((s) => s.user)

  // Viewers cannot upload; only editors and admins can
  const canUpload = user?.role === ROLES.EDITOR || user?.role === ROLES.ADMIN

  const handleUploadComplete = () => {
    setShowUpload(false)
    setRefreshKey(prev => prev + 1)
  }

  return (
    <div className="h-full flex flex-col">
      <div className={`flex items-center justify-between px-6 py-4 border-b ${
        isDark ? 'border-slate-800' : 'border-gray-200 bg-white'
      }`}>
        <div>
          <h1 className={`text-xl font-semibold ${isDark ? 'text-white' : 'text-gray-900'}`}>Documents</h1>
          <p className={`text-sm mt-0.5 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
            {canUpload
              ? 'Manage your knowledge base documents'
              : 'Browse your organization\'s knowledge base (view only)'}
          </p>
        </div>
        {canUpload && (
          <button
            onClick={() => setShowUpload(true)}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2"
          >
            <span>+</span>
            <span>Upload Document</span>
          </button>
        )}
      </div>

      <div className="flex-1 overflow-auto">
        <DocumentList key={refreshKey} />
      </div>

      {showUpload && canUpload && (
        <DocumentUpload
          onClose={() => setShowUpload(false)}
          onComplete={handleUploadComplete}
        />
      )}
    </div>
  )
}