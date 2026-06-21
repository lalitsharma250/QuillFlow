import { useState } from 'react'
import type { Source } from '@/lib/types'
import { useThemeStore } from '@/stores/themeStore'

const MIN_RELEVANCE_THRESHOLD = 0.1

interface SourceCardProps {
  sources: Source[]
  answerText?: string
  /** Stable per-message id so citation anchors don't collide across messages. */
  messageId: string
}

/** A source paired with its original (pre-dedup) citation index. */
interface IndexedSource {
  source: Source
  index: number // 0-based; citation [n] => index n-1
}

/** A display group: one document, possibly spanning multiple cited chunks. */
interface SourceGroup {
  filename: string
  members: IndexedSource[]
  topScore: number
  cited: boolean
}

function scoreBadgeClasses(score: number, isDark: boolean): string {
  if (score >= 0.9) return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
  if (score >= 0.7) return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
  if (score >= 0.5) return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300'
  return isDark ? 'bg-slate-700 text-slate-400' : 'bg-gray-100 text-gray-500'
}

export default function SourceCard({ sources, answerText, messageId }: SourceCardProps) {
  const isDark = useThemeStore((s) => s.theme === 'dark')
  const [showAll, setShowAll] = useState(false)

  if (!sources || sources.length === 0) return null

  // Keep original indices BEFORE any filtering so [n] alignment is preserved.
  const indexed: IndexedSource[] = sources.map((source, index) => ({ source, index }))

  // Drop near-zero-relevance noise.
  const relevant = indexed.filter((s) => s.source.relevance_score >= MIN_RELEVANCE_THRESHOLD)
  if (relevant.length === 0) return null

  // Parse cited [n] markers, clamped to the valid source range.
  const citedIndices = new Set<number>()
  if (answerText) {
    const citationPattern = /\[(\d+)\]/g
    let match: RegExpExecArray | null
    while ((match = citationPattern.exec(answerText)) !== null) {
      const idx = parseInt(match[1], 10) - 1
      if (idx >= 0 && idx < sources.length) citedIndices.add(idx)
    }
  }

  // Group by filename for display, preserving per-chunk original indices.
  const groupMap = new Map<string, SourceGroup>()
  for (const item of relevant) {
    const key = item.source.filename
    let group = groupMap.get(key)
    if (!group) {
      group = { filename: key, members: [], topScore: 0, cited: false }
      groupMap.set(key, group)
    }
    group.members.push(item)
    group.topScore = Math.max(group.topScore, item.source.relevance_score)
    if (citedIndices.has(item.index)) group.cited = true
  }

  const groups = Array.from(groupMap.values())
  const citedGroups = groups.filter((g) => g.cited)
  const uncitedGroups = groups.filter((g) => !g.cited)

  const citedCount = citedIndices.size
  const hasCitations = citedCount > 0

  // Default view: cited groups only. If nothing is cited, fall back to showing
  // all retrieved sources (so the card is still useful for non-citing answers).
  const visibleGroups = hasCitations
    ? showAll
      ? [...citedGroups, ...uncitedGroups]
      : citedGroups
    : groups

  const hiddenCount = uncitedGroups.length

  const renderGroup = (group: SourceGroup) => {
    // Representative chunk = highest scoring member; citation badges show all.
    const rep = group.members.reduce((a, b) =>
      b.source.relevance_score > a.source.relevance_score ? b : a
    )
    const pages = Array.from(
      new Set(group.members.map((m) => m.source.page_number).filter((p): p is number => p != null))
    ).sort((a, b) => a - b)

    return (
      <div
        key={group.filename}
        id={`src-${messageId}-${rep.index}`}
        className={`px-3 py-2 transition-colors scroll-mt-20 ${
          group.cited
            ? isDark ? 'bg-blue-900/10' : 'bg-blue-50/50'
            : ''
        } ${isDark ? 'hover:bg-slate-800/50' : 'hover:bg-gray-50'}`}
      >
        <div className="flex items-center justify-between mb-1 gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {/* Citation number chips for every cited chunk in this file */}
            <span className="flex items-center gap-1 flex-shrink-0">
              {group.members.map((m) => {
                const cited = citedIndices.has(m.index)
                return (
                  <span
                    key={m.index}
                    id={`src-${messageId}-${m.index}`}
                    className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-medium ${
                      cited
                        ? 'bg-blue-600 text-white'
                        : isDark ? 'bg-slate-700 text-slate-400' : 'bg-gray-200 text-gray-500'
                    }`}
                  >
                    {m.index + 1}
                  </span>
                )
              })}
            </span>
            <span className={`text-xs font-medium truncate ${isDark ? 'text-slate-300' : 'text-gray-700'}`}>
              {group.filename}
              {rep.source.section_heading && (
                <span className={isDark ? 'text-slate-500' : 'text-gray-400'}> › {rep.source.section_heading}</span>
              )}
              {pages.length > 0 && (
                <span className={isDark ? 'text-slate-500' : 'text-gray-400'}>
                  {' '}· p. {pages.join(', ')}
                </span>
              )}
            </span>
            {group.cited && <span className="text-xs text-blue-500 flex-shrink-0">✓ cited</span>}
          </div>
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium flex-shrink-0 ${scoreBadgeClasses(group.topScore, isDark)}`}>
            {(group.topScore * 100).toFixed(0)}% match
          </span>
        </div>
        <p className={`text-xs line-clamp-2 ml-7 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
          {rep.source.chunk_text_preview}
        </p>
      </div>
    )
  }

  return (
    <div className={`mt-3 border rounded-lg overflow-hidden ${isDark ? 'border-slate-700' : 'border-gray-200'}`}>
      <div className={`px-3 py-2 border-b ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-gray-50 border-gray-200'}`}>
        <span className={`text-xs font-medium ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
          📄 {hasCitations ? `${citedCount} Cited` : `${groups.length} ${groups.length === 1 ? 'Source' : 'Sources'}`}
        </span>
      </div>

      <div className={`divide-y ${isDark ? 'divide-slate-700' : 'divide-gray-100'}`}>
        {visibleGroups.map(renderGroup)}
      </div>

      {hasCitations && hiddenCount > 0 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className={`w-full px-3 py-2 text-xs font-medium border-t text-left transition-colors ${
            isDark
              ? 'border-slate-700 text-slate-400 hover:bg-slate-800/50'
              : 'border-gray-200 text-gray-500 hover:bg-gray-50'
          }`}
        >
          {showAll
            ? '▴ Hide retrieved sources'
            : `▾ Show ${hiddenCount} more retrieved ${hiddenCount === 1 ? 'source' : 'sources'}`}
        </button>
      )}
    </div>
  )
}
