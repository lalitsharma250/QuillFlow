// frontend/src/components/ErrorBoundary.tsx
import React, { Component } from 'react'
import type { ErrorInfo } from 'react'


interface Props {
  children: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo)
  }

  handleReload = () => {
    localStorage.clear()
    window.location.href = '/login'
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
          <div className="max-w-md w-full bg-slate-800 border border-slate-700 rounded-xl p-6 text-center">
            <div className="text-5xl mb-3">⚠️</div>
            <h2 className="text-xl font-semibold text-white mb-2">Something went wrong</h2>
            <p className="text-slate-400 text-sm mb-4">
              Your session may have expired or permissions changed. Please log in again.
            </p>
            <p className="text-xs text-slate-500 mb-4 font-mono bg-slate-900 p-2 rounded break-all">
              {this.state.error?.message || 'Unknown error'}
            </p>
            <button
              onClick={this.handleReload}
              className="w-full py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg"
            >
              Log in again
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}