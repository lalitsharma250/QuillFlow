import { useThemeStore } from '@/stores/themeStore'

interface DocumentStatusProps {
  status: string
  size?: 'sm' | 'md'
}

export default function DocumentStatus({ status, size = 'sm' }: DocumentStatusProps) {
  const isDark = useThemeStore((s) => s.theme === 'dark')

  const config: Record<string, { light: string; dark: string; label: string; dot: string }> = {
    indexed: {
      light: 'bg-green-50 text-green-700 border border-green-200',
      dark: 'bg-green-900/30 text-green-400',
      label: 'Indexed',
      dot: 'bg-green-500',
    },
    processing: {
      light: 'bg-blue-50 text-blue-700 border border-blue-200',
      dark: 'bg-blue-900/30 text-blue-400',
      label: 'Processing',
      dot: 'bg-blue-500 animate-pulse',
    },
    pending: {
      light: 'bg-yellow-50 text-yellow-700 border border-yellow-200',
      dark: 'bg-yellow-900/30 text-yellow-400',
      label: 'Pending',
      dot: 'bg-yellow-500',
    },
    failed: {
      light: 'bg-red-50 text-red-700 border border-red-200',
      dark: 'bg-red-900/30 text-red-400',
      label: 'Failed',
      dot: 'bg-red-500',
    },
    superseded: {
      light: 'bg-gray-50 text-gray-500 border border-gray-200',
      dark: 'bg-slate-800 text-slate-500',
      label: 'Superseded',
      dot: 'bg-gray-400',
    },
  }

  const c = config[status] || config.pending
  const sizeClasses = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1'

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-medium ${sizeClasses} ${
      isDark ? c.dark : c.light
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {c.label}
    </span>
  )
}