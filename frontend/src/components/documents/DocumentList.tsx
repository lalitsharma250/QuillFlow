import { useState, useEffect } from 'react'
import { documentsApi } from '@/api/documents'
import { adminApi } from '@/api/admin'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { toast } from '@/components/ui/toaster'
import DocumentStatus from './DocumentStatus'
import { formatDate } from '@/lib/utils'
import { ROLES } from '@/lib/constants'
import type { Document } from '@/lib/types'

export default function DocumentList() {
  const [documents, setDocuments] = useState<Document[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [search, setSearch] = useState('')
  const user = useAuthStore((s) => s.user)
  const isDark = useThemeStore((s) => s.theme === 'dark')

  const pageSize = 20

  const fetchDocuments = async () => {
    setLoading(true)
    try {
      const data = await documentsApi.list(page, pageSize, statusFilter || undefined)
      setDocuments(data.documents)
      setTotal(data.total)
    } catch {
      toast('Failed to load documents', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchDocuments() }, [page, statusFilter])

  useEffect(() => {
    const hasProcessing = documents.some(d => d.status === 'processing' || d.status === 'pending')
    if (!hasProcessing) return
    const interval = setInterval(fetchDocuments, 3000)
    return () => clearInterval(interval)
  }, [documents])

  const handleDelete = async (doc: Document) => {
    if (!confirm(`Delete "${doc.filename}"? This removes its chunks from the vector store.`)) return
    try {
      await adminApi.deleteDocument(doc.document_id)
      toast(`"${doc.filename}" deleted`, 'success')
      fetchDocuments()
    } catch (err: any) {
      toast(err.response?.data?.detail || 'Delete failed', 'error')
    }
  }

  const filteredDocs = search
    ? documents.filter(d => d.filename.toLowerCase().includes(search.toLowerCase()))
    : documents

  const totalPages = Math.ceil(total / pageSize)

  const contentTypeIcon: Record<string, string> = {
    text: '📝', html: '🌐', markdown: '📋', pdf: '📕',
  }

  return (
    <div className="p-6">
      {/* Filters */}
      <div className="flex items-center gap-3 mb-4">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search documents..."
          className={`flex-1 max-w-sm px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${
            isDark
              ? 'bg-slate-800 border-slate-700 text-white placeholder-slate-500'
              : 'bg-white border-gray-300 text-gray-900 placeholder-gray-400'
          }`}
        />
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
          className={`px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${
            isDark
              ? 'bg-slate-800 border-slate-700 text-white'
              : 'bg-white border-gray-300 text-gray-900'
          }`}
        >
          <option value="">All Status</option>
          <option value="indexed">Indexed</option>
          <option value="processing">Processing</option>
          <option value="pending">Pending</option>
          <option value="failed">Failed</option>
        </select>
        <span className={`text-sm ${isDark ? 'text-slate-500' : 'text-gray-500'}`}>
          {total} document{total !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Table */}
      {loading ? (
        <div className="space-y-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className={`h-14 rounded-lg animate-pulse ${isDark ? 'bg-slate-800/50' : 'bg-gray-100'}`} />
          ))}
        </div>
      ) : filteredDocs.length === 0 ? (
        <div className="text-center py-12">
          <span className="text-4xl mb-3 block">📄</span>
          <p className={isDark ? 'text-slate-400' : 'text-gray-500'}>No documents found</p>
          <p className={`text-sm mt-1 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
            Upload documents to build your knowledge base
          </p>
        </div>
      ) : (
        <div className={`border rounded-lg overflow-hidden ${
          isDark ? 'border-slate-800' : 'border-gray-200'
        }`}>
          <table className="w-full">
            <thead>
              <tr className={isDark ? 'bg-slate-800/50' : 'bg-gray-50'}>
                <th className={`text-left px-4 py-3 text-xs font-medium uppercase tracking-wider ${
                  isDark ? 'text-slate-400' : 'text-gray-500'
                }`}>Document</th>
                <th className={`text-left px-4 py-3 text-xs font-medium uppercase tracking-wider ${
                  isDark ? 'text-slate-400' : 'text-gray-500'
                }`}>Type</th>
                <th className={`text-left px-4 py-3 text-xs font-medium uppercase tracking-wider ${
                  isDark ? 'text-slate-400' : 'text-gray-500'
                }`}>Status</th>
                <th className={`text-left px-4 py-3 text-xs font-medium uppercase tracking-wider ${
                  isDark ? 'text-slate-400' : 'text-gray-500'
                }`}>Chunks</th>
                <th className={`text-left px-4 py-3 text-xs font-medium uppercase tracking-wider ${
                  isDark ? 'text-slate-400' : 'text-gray-500'
                }`}>Version</th>
                <th className={`text-left px-4 py-3 text-xs font-medium uppercase tracking-wider ${
                  isDark ? 'text-slate-400' : 'text-gray-500'
                }`}>Date</th>
                {user?.role === ROLES.ADMIN && (
                  <th className={`text-right px-4 py-3 text-xs font-medium uppercase tracking-wider ${
                    isDark ? 'text-slate-400' : 'text-gray-500'
                  }`}>Actions</th>
                )}
              </tr>
            </thead>
            <tbody className={`divide-y ${isDark ? 'divide-slate-800' : 'divide-gray-100'}`}>
              {filteredDocs.map((doc) => (
                <tr key={doc.document_id} className={`transition-colors ${
                  isDark ? 'hover:bg-slate-800/30' : 'hover:bg-gray-50'
                }`}>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span>{contentTypeIcon[doc.content_type] || '📄'}</span>
                      <span className={`text-sm font-medium ${isDark ? 'text-white' : 'text-gray-900'}`}>
                        {doc.filename}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs uppercase px-2 py-0.5 rounded ${
                      isDark ? 'text-slate-400 bg-slate-800' : 'text-gray-500 bg-gray-100'
                    }`}>
                      {doc.content_type}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <DocumentStatus status={doc.status} />
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-sm ${isDark ? 'text-slate-300' : 'text-gray-600'}`}>
                      {doc.chunk_count ?? '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-sm ${isDark ? 'text-slate-300' : 'text-gray-600'}`}>
                      v{doc.version}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
                      {formatDate(doc.created_at)}
                    </span>
                  </td>
                  {user?.role === ROLES.ADMIN && (
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleDelete(doc)}
                        className="text-xs text-gray-400 hover:text-red-500 transition-colors"
                      >
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className={`text-sm ${isDark ? 'text-slate-500' : 'text-gray-500'}`}>
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              className={`px-3 py-1.5 border rounded-lg text-sm disabled:opacity-50 disabled:cursor-not-allowed ${
                isDark
                  ? 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'
                  : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
              }`}
            >
              Previous
            </button>
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className={`px-3 py-1.5 border rounded-lg text-sm disabled:opacity-50 disabled:cursor-not-allowed ${
                isDark
                  ? 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'
                  : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
              }`}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}