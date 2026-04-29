import { useState, useRef } from 'react'
import { documentsApi } from '@/api/documents'
import { toast } from '@/components/ui/toaster'
import { useThemeStore } from '@/stores/themeStore'

interface DocumentUploadProps {
  onClose: () => void
  onComplete: () => void
}

const SUPPORTED_EXTENSIONS = ['txt', 'html', 'htm', 'md', 'markdown', 'pdf']
const MAX_FILE_SIZE = 20 * 1024 * 1024 // 20MB
const MAX_FILES = 20

export default function DocumentUpload({ onClose, onComplete }: DocumentUploadProps) {
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const isDark = useThemeStore((s) => s.theme === 'dark')

  const getExtension = (filename: string): string => {
    return (filename.split('.').pop() || '').toLowerCase()
  }

  const validateFile = (file: File): string | null => {
    const ext = getExtension(file.name)
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      return `Unsupported type (.${ext})`
    }
    if (file.size > MAX_FILE_SIZE) {
      return 'File too large (max 20MB)'
    }
    return null
  }

  const handleFiles = (fileList: FileList) => {
    const newFiles: File[] = []

    for (const file of Array.from(fileList)) {
      const error = validateFile(file)
      if (error) {
        toast(`"${file.name}" — ${error}`, 'error')
        continue
      }

      // Check for duplicates
      if (files.some(f => f.name === file.name && f.size === file.size)) {
        toast(`"${file.name}" already added`, 'info')
        continue
      }

      newFiles.push(file)
    }

    const total = files.length + newFiles.length
    if (total > MAX_FILES) {
      toast(`Maximum ${MAX_FILES} files per upload`, 'error')
      return
    }

    setFiles(prev => [...prev, ...newFiles])
  }

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index))
  }

  const handleUpload = async () => {
  if (files.length === 0) return
  setUploading(true)

  try {
    if (files.length === 1) {
      setProgress(`Uploading "${files[0].name}"...`)
      await documentsApi.uploadFile(files[0])
      toast(`"${files[0].name}" uploaded successfully`, 'success')
    } else {
      setProgress(`Uploading ${files.length} files...`)
      await documentsApi.uploadFiles(files)
      toast(`${files.length} documents uploaded successfully`, 'success')
    }
    onComplete()
  } catch (err: any) {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    
    // Handle specific error codes with friendly messages
    if (status === 403) {
      toast(
        'Your role does not allow uploading documents. Contact an admin to upgrade to Editor role.',
        'error'
      )
    } else if (status === 413) {
      toast('File too large. Maximum size is 20MB per file.', 'error')
    } else if (typeof detail === 'string') {
      toast(detail, 'error')
    } else if (Array.isArray(detail)) {
      toast(detail.map((d: any) => d.msg || JSON.stringify(d)).join(', '), 'error')
    } else {
      toast('Upload failed. Please try again.', 'error')
    }
  } finally {
    setUploading(false)
    setProgress('')
  }
}

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const contentTypeIcon: Record<string, string> = {
    pdf: '📕', txt: '📝', text: '📝', html: '🌐', htm: '🌐', md: '📋', markdown: '📋',
  }

  const totalSize = files.reduce((sum, f) => sum + f.size, 0)

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className={`border rounded-xl shadow-2xl w-full max-w-lg ${
        isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-gray-200'
      }`}>
        {/* Header */}
        <div className={`flex items-center justify-between px-6 py-4 border-b ${
          isDark ? 'border-slate-700' : 'border-gray-200'
        }`}>
          <div>
            <h2 className={`text-lg font-semibold ${isDark ? 'text-white' : 'text-gray-900'}`}>
              Upload Documents
            </h2>
            <p className={`text-xs mt-0.5 ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
              Direct file upload • No size conversion overhead
            </p>
          </div>
          <button
            onClick={onClose}
            className={`text-xl ${isDark ? 'text-slate-400 hover:text-white' : 'text-gray-400 hover:text-gray-900'}`}
          >
            ×
          </button>
        </div>

        {/* Content */}
        <div className="p-6">
          {/* Drop Zone */}
          <div
            onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files) }}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
              dragOver
                ? 'border-blue-500 bg-blue-500/10'
                : isDark
                  ? 'border-slate-600 hover:border-slate-500 hover:bg-slate-700/30'
                  : 'border-gray-300 hover:border-gray-400 hover:bg-gray-50'
            }`}
          >
            <span className="text-3xl mb-2 block">📁</span>
            <p className={`text-sm font-medium ${isDark ? 'text-slate-300' : 'text-gray-700'}`}>
              Drop files here or click to browse
            </p>
            <p className={`text-xs mt-1 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
              PDF, TXT, HTML, Markdown • Max 20MB per file • Up to {MAX_FILES} files
            </p>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.html,.htm,.md,.markdown,.pdf"
            onChange={(e) => e.target.files && handleFiles(e.target.files)}
            className="hidden"
          />

          {/* File List */}
          {files.length > 0 && (
            <div className="mt-4 space-y-2 max-h-48 overflow-y-auto">
              {files.map((file, i) => {
                const ext = getExtension(file.name)
                return (
                  <div
                    key={`${file.name}-${file.size}`}
                    className={`flex items-center justify-between px-3 py-2 rounded-lg ${
                      isDark ? 'bg-slate-900' : 'bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span>{contentTypeIcon[ext] || '📄'}</span>
                      <span className={`text-sm truncate ${isDark ? 'text-white' : 'text-gray-900'}`}>
                        {file.name}
                      </span>
                      <span className={`text-xs flex-shrink-0 ${isDark ? 'text-slate-500' : 'text-gray-400'}`}>
                        {formatSize(file.size)}
                      </span>
                    </div>
                    <button
                      onClick={() => removeFile(i)}
                      className="text-gray-400 hover:text-red-500 ml-2 flex-shrink-0"
                    >
                      ×
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {/* Upload Progress */}
          {uploading && progress && (
            <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${
              isDark ? 'bg-blue-900/20 text-blue-400' : 'bg-blue-50 text-blue-600'
            }`}>
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                {progress}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className={`flex items-center justify-between px-6 py-4 border-t ${
          isDark ? 'border-slate-700' : 'border-gray-200'
        }`}>
          <span className={`text-sm ${isDark ? 'text-slate-400' : 'text-gray-500'}`}>
            {files.length} file{files.length !== 1 ? 's' : ''}
            {files.length > 0 && ` • ${formatSize(totalSize)}`}
          </span>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              disabled={uploading}
              className={`px-4 py-2 text-sm rounded-lg ${
                isDark ? 'bg-slate-700 hover:bg-slate-600 text-white' : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
              }`}
            >
              Cancel
            </button>
            <button
              onClick={handleUpload}
              disabled={files.length === 0 || uploading}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
            >
              {uploading ? 'Uploading...' : `Upload ${files.length > 1 ? `${files.length} Files` : 'File'}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}