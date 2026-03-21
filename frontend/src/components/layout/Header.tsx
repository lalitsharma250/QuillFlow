import { useLocation } from 'react-router-dom'

export function Header() {
  const location = useLocation()

  return (
    <header className="qf-header">
      <div>
        <p className="qf-eyebrow">Current Section</p>
        <strong>{location.pathname.replace('/', '') || 'chat'}</strong>
      </div>
    </header>
  )
}
