import { useEffect, useState } from 'react'

interface Toast {
  id: string
  message: string
  type: 'success' | 'error' | 'info'
}

let toastListeners: ((toast: Toast) => void)[] = []

export function toast(message: any, type: 'success' | 'error' | 'info' = 'info') {
  // Ensure message is always a string
  let msg: string
  if (typeof message === 'string') {
    msg = message
  } else if (message?.detail) {
    msg = typeof message.detail === 'string' ? message.detail : JSON.stringify(message.detail)
  } else if (message?.message) {
    msg = message.message
  } else {
    msg = JSON.stringify(message)
  }

  const t: Toast = { id: crypto.randomUUID(), message: msg, type }
  toastListeners.forEach(fn => fn(t))
}

export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([])

  useEffect(() => {
    const listener = (t: Toast) => {
      setToasts(prev => [...prev, t])
      setTimeout(() => {
        setToasts(prev => prev.filter(x => x.id !== t.id))
      }, 3000)
    }
    toastListeners.push(listener)
    return () => {
      toastListeners = toastListeners.filter(fn => fn !== listener)
    }
  }, [])

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map(t => (
        <div
          key={t.id}
          className={`px-4 py-3 rounded-lg shadow-lg text-white text-sm max-w-md ${
            t.type === 'error' ? 'bg-red-600' :
            t.type === 'success' ? 'bg-green-600' :
            'bg-slate-800'
          }`}
        >
          {t.message}
        </div>
      ))}
    </div>
  )
}