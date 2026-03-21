import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/components/ui/toaster'
import { ROUTES } from '@/lib/constants'

type AuthTab = 'signin' | 'signup'

export default function LoginPage() {
  const [tab, setTab] = useState<AuthTab>('signin')
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  // Redirect if already logged in (in useEffect, not during render)
  useEffect(() => {
    if (isAuthenticated) {
      navigate(ROUTES.CHAT, { replace: true })
    }
  }, [isAuthenticated, navigate])

  // Don't render login form if authenticated
  if (isAuthenticated) return null

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
      <div className="w-full max-w-md px-4">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 mb-2">
            <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-xl">Q</span>
            </div>
            <h1 className="text-3xl font-bold text-white">QuillFlow</h1>
          </div>
          <p className="text-slate-400 text-sm">Intelligent RAG-powered content generation</p>
        </div>

        {/* Card */}
        <div className="bg-slate-800 border border-slate-700 rounded-xl shadow-2xl p-6">
          {/* Tabs */}
          <div className="flex mb-6 bg-slate-900 rounded-lg p-1">
            <button
              onClick={() => setTab('signin')}
              className={`flex-1 py-2 text-sm font-medium rounded-md transition-colors ${
                tab === 'signin'
                  ? 'bg-slate-700 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Sign In
            </button>
            <button
              onClick={() => setTab('signup')}
              className={`flex-1 py-2 text-sm font-medium rounded-md transition-colors ${
                tab === 'signup'
                  ? 'bg-slate-700 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Sign Up
            </button>
          </div>

          {tab === 'signin' ? (
            <SignInForm
              onSuccess={(data) => {
                setAuth(data.access_token, data.refresh_token, data.user)
                toast('Welcome back!', 'success')
                navigate(ROUTES.CHAT, { replace: true })
              }}
            />
          ) : (
            <SignUpForm
              onSuccess={(data) => {
                setAuth(data.access_token, data.refresh_token, data.user)
                toast('Account created!', 'success')
                navigate(ROUTES.CHAT, { replace: true })
              }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

// ── Sign In Form ──────────────────────────────────────

function SignInForm({ onSuccess }: { onSuccess: (data: any) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [apiKey, setApiKey] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const data = await authApi.login(email, password)
      onSuccess(data)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleApiKeyLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const data = await authApi.loginWithApiKey(apiKey)
      onSuccess(data)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Invalid API key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
            className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
            className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        {error && (
          <div className="text-red-400 text-sm bg-red-900/20 border border-red-800 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
        >
          {loading ? 'Signing in...' : 'Sign In'}
        </button>
      </form>

      {/* Divider */}
      <div className="relative my-6">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-slate-700"></div>
        </div>
        <div className="relative flex justify-center text-xs">
          <span className="bg-slate-800 px-2 text-slate-500">or</span>
        </div>
      </div>

      {/* API Key Login */}
      <button
        onClick={() => setShowApiKey(!showApiKey)}
        className="w-full text-sm text-slate-400 hover:text-white transition-colors text-center"
      >
        {showApiKey ? 'Hide API key login' : 'Sign in with API Key →'}
      </button>

      {showApiKey && (
        <form onSubmit={handleApiKeyLogin} className="mt-4 space-y-3">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="qf-your-api-key..."
            required
            className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
          />
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 bg-slate-700 hover:bg-slate-600 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
          >
            {loading ? 'Verifying...' : 'Login with API Key'}
          </button>
        </form>
      )}
    </div>
  )
}

// ── Sign Up Form ──────────────────────────────────────

function SignUpForm({
  onSuccess,
}: {
  onSuccess: (data: any) => void
}) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [inviteStatus, setInviteStatus] = useState<{
    valid: boolean
    message: string
    org_name?: string
    role?: string
  } | null>(null)
  const [verifying, setVerifying] = useState(false)

  // Verify invite code when user finishes typing
  const verifyInvite = async (code: string) => {
    if (code.length < 3) {
      setInviteStatus(null)
      return
    }

    setVerifying(true)
    try {
      const result = await authApi.verifyInvite(code)
      setInviteStatus({
        valid: result.valid,
        message: result.message,
        org_name: result.org_name || undefined,
        role: result.role || undefined,
      })
    } catch {
      setInviteStatus({ valid: false, message: 'Failed to verify code' })
    } finally {
      setVerifying(false)
    }
  }

  const handleInviteChange = (value: string) => {
    setInviteCode(value)
    // Debounce verification
    const timeout = setTimeout(() => verifyInvite(value), 500)
    return () => clearTimeout(timeout)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!inviteStatus?.valid) {
      setError('Please enter a valid invite code')
      return
    }

    setLoading(true)

    try {
      const data = await authApi.signup({
        email,
        password,
        name,
        invite_code: inviteCode,
      })
      onSuccess(data)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Signup failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Invite Code — First field */}
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Invite Code</label>
          <input
            type="text"
            value={inviteCode}
            onChange={(e) => handleInviteChange(e.target.value)}
            placeholder="INV-xxxxxxxx"
            required
            className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent tracking-wider"
          />
          {verifying && (
            <p className="text-slate-500 text-xs mt-1">Verifying...</p>
          )}
          {inviteStatus && !verifying && (
            <p className={`text-xs mt-1 ${inviteStatus.valid ? 'text-green-400' : 'text-red-400'}`}>
              {inviteStatus.valid ? '✓' : '✗'} {inviteStatus.message}
            </p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Full Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="John Doe"
            required
            className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
            className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            Password <span className="text-slate-500">(min 8 characters)</span>
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
            minLength={8}
            className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        {error && (
          <div className="text-red-400 text-sm bg-red-900/20 border border-red-800 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading || !inviteStatus?.valid}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-700 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
        >
          {loading ? 'Creating account...' : 'Create Account'}
        </button>
      </form>

      <p className="text-center text-slate-500 text-xs mt-4">
        Don't have an invite code?{' '}
        <span className="text-slate-400">Contact your organization admin.</span>
      </p>
    </div>
  )
}