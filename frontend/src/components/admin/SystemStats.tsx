import { useState, useEffect } from 'react'
import { adminApi } from '@/api/admin'
import { toast } from '@/components/ui/toaster'
import { useThemeStore } from '@/stores/themeStore'
import type { SystemStats as SystemStatsType } from '@/lib/types'

export default function SystemStats() {
  const [stats, setStats] = useState<SystemStatsType | null>(null)
  const [loading, setLoading] = useState(true)
  const isDark = useThemeStore((s) => s.theme === 'dark')

  const fetchStats = async () => {
    try {
      const data = await adminApi.getStats()
      setStats(data)
    } catch {
      toast('Failed to load stats', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchStats() }, [])

  const handleClearCache = async () => {
    if (!confirm('Clear all cached responses?')) return
    try { await adminApi.clearCache(); toast('Cache cleared', 'success'); fetchStats() }
    catch { toast('Failed to clear cache', 'error') }
  }

  const handleCleanDocs = async () => {
    if (!confirm('Delete all pending/failed documents?')) return
    try { const r = await adminApi.cleanStaleDocuments(); toast(r.message, 'success'); fetchStats() }
    catch { toast('Cleanup failed', 'error') }
  }

  const handleCleanJobs = async () => {
    if (!confirm('Delete all stale ingestion jobs?')) return
    try { const r = await adminApi.cleanStaleJobs(); toast(r.message, 'success'); fetchStats() }
    catch { toast('Cleanup failed', 'error') }
  }

  if (loading) {
    return (
      <div className="p-6 space-y-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className={`h-24 rounded-xl animate-pulse ${isDark ? 'bg-slate-800/50' : 'bg-gray-100'}`} />
        ))}
      </div>
    )
  }

  if (!stats) return null

  return (
    <div className="p-6 space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon="📄" label="Documents" value={stats.documents.total_indexed} sub="indexed" isDark={isDark} />
        <StatCard icon="🧩" label="Chunks" value={stats.documents.total_chunks} sub="in vector store" isDark={isDark} />
        <StatCard icon="🗄️" label="Cache" value={stats.cache.org_keys ?? stats.cache.keys ?? 0} sub={stats.cache.memory_used} isDark={isDark} />
        <StatCard icon="📦" label="Vectors" value={stats.vector_store.org_points ?? stats.vector_store.points_count ?? 0} sub={stats.vector_store.status || 'unknown'} isDark={isDark} />
      </div>

      {Object.keys(stats.documents.by_status).length > 0 && (
        <div className={`border rounded-xl p-4 ${
          isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-gray-200 shadow-sm'
        }`}>
          <h3 className={`text-sm font-medium mb-3 ${isDark ? 'text-slate-300' : 'text-gray-700'}`}>
            Documents by Status
          </h3>
          <div className="flex gap-4">
            {Object.entries(stats.documents.by_status).map(([status, count]) => (
              <div key={status} className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${
                  status === 'indexed' ? 'bg-green-500' :
                  status === 'processing' ? 'bg-blue-500' :
                  status === 'pending' ? 'bg-yellow-500' : 'bg-red-500'
                }`} />
                <span className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-600'}`}>
                  {status}: {count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className={`border rounded-xl p-4 ${
        isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-gray-200 shadow-sm'
      }`}>
        <h3 className={`text-sm font-medium mb-3 ${isDark ? 'text-slate-300' : 'text-gray-700'}`}>
          System Actions
        </h3>
        <div className="flex flex-wrap gap-3">
          <button onClick={handleClearCache} className={`px-4 py-2 text-sm rounded-lg transition-colors ${
            isDark ? 'bg-slate-700 hover:bg-slate-600 text-white' : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
          }`}>🗑️ Clear Cache</button>
          <button onClick={handleCleanDocs} className={`px-4 py-2 text-sm rounded-lg transition-colors ${
            isDark ? 'bg-slate-700 hover:bg-slate-600 text-white' : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
          }`}>🧹 Clean Stale Documents</button>
          <button onClick={handleCleanJobs} className={`px-4 py-2 text-sm rounded-lg transition-colors ${
            isDark ? 'bg-slate-700 hover:bg-slate-600 text-white' : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
          }`}>🧹 Clean Stale Jobs</button>
          <button onClick={fetchStats} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors">
            🔄 Refresh Stats
          </button>
        </div>
      </div>
    </div>
  )
}

function StatCard({ icon, label, value, sub, isDark }: {
  icon: string; label: string; value: number | string; sub: string; isDark: boolean
}) {
  return (
    <div className={`border rounded-xl p-4 ${
      isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-gray-200 shadow-sm'
    }`}>
      <div className="flex items-center gap-2 mb-2">
        <span>{icon}</span>
        <span className={`text-xs uppercase tracking-wider ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
          {label}
        </span>
      </div>
      <p className={`text-2xl font-bold ${isDark ? 'text-white' : 'text-gray-900'}`}>{value}</p>
      <p className={`text-xs mt-0.5 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>{sub}</p>
    </div>
  )
}