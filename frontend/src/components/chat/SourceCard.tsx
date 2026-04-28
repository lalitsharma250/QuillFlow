import type { Source } from '@/lib/types'
import { useThemeStore } from '@/stores/themeStore'

const MIN_RELEVANCE_THRESHOLD = 0.1

interface SourceCardProps {
  sources: Source[]
  answerText?: string  // ← NEW: pass answer text to filter by citations
}

export default function SourceCard({ sources, answerText }: SourceCardProps) {
  const isDark = useThemeStore((s) => s.theme === 'dark')

  if (!sources || sources.length === 0) return null

  const relevantSources = sources.filter(s => s.relevance_score >= MIN_RELEVANCE_THRESHOLD)
  if (relevantSources.length === 0) return null

  // Find which sources are cited
  const citedIndices = new Set<number>()
  if (answerText) {
    const citationPattern = /\[(\d+)\]/g
    let match
    while ((match = citationPattern.exec(answerText)) !== null) {
      citedIndices.add(parseInt(match[1]) - 1)
    }
  }

  const citedCount = citedIndices.size

  return (
    <div className={`mt-3 border rounded-lg overflow-hidden ${
      isDark ? 'border-slate-700' : 'border-gray-200'
    }`}>
      <div className={`px-3 py-2 border-b ${
        isDark ? 'bg-slate-800 border-slate-700' : 'bg-gray-50 border-gray-200'
      }`}>
        <span className={`text-xs font-medium ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
          📄 {citedCount > 0 ? `${citedCount} Cited` : `${relevantSources.length} Sources`}
          {citedCount > 0 && citedCount < relevantSources.length && (
            <span className="ml-1 text-slate-500">
              · {relevantSources.length - citedCount} additional
            </span>
          )}
        </span>
      </div>
      <div className={`divide-y ${isDark ? 'divide-slate-700' : 'divide-gray-100'}`}>
        {relevantSources.map((source, i) => {
          const isCited = citedIndices.has(i)
          return (
            <div
              key={i}
              className={`px-3 py-2 transition-colors ${
                isCited
                  ? isDark ? 'bg-blue-900/10' : 'bg-blue-50/50'
                  : 'opacity-60'
              } ${isDark ? 'hover:bg-slate-800/50' : 'hover:bg-gray-50'}`}
            >
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-medium flex-shrink-0 ${
                    isCited
                      ? 'bg-blue-600 text-white'
                      : isDark ? 'bg-slate-700 text-slate-400' : 'bg-gray-200 text-gray-500'
                  }`}>
                    {i + 1}
                  </span>
                  <span className={`text-xs font-medium ${isDark ? 'text-slate-300' : 'text-gray-700'}`}>
                    {source.filename}
                    {source.section_heading && (
                      <span className={isDark ? 'text-slate-500' : 'text-gray-400'}> › {source.section_heading}</span>
                    )}
                  </span>
                  {isCited && (
                    <span className="text-xs text-blue-500">✓ cited</span>
                  )}
                </div>
                <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                  source.relevance_score >= 0.9 ? 'bg-green-100 text-green-700' :
                  source.relevance_score >= 0.7 ? 'bg-emerald-100 text-emerald-700' :
                  source.relevance_score >= 0.5 ? 'bg-yellow-100 text-yellow-700' :
                  isDark ? 'bg-slate-700 text-slate-400' : 'bg-gray-100 text-gray-500'
                }`}>
                  {(source.relevance_score * 100).toFixed(0)}% match
                </span>
              </div>
              <p className={`text-xs line-clamp-2 ml-7 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
                {source.chunk_text_preview}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}