import { NavLink } from 'react-router-dom'

const links = [
  { label: 'Chat', to: '/chat' },
  { label: 'Documents', to: '/documents' },
  { label: 'Admin', to: '/admin' },
]

export function Sidebar() {
  return (
    <aside className="qf-sidebar">
      <div>
        <p className="qf-eyebrow">Workspace</p>
        <h1>QuillFlow</h1>
      </div>
      <nav className="qf-nav">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) => (isActive ? 'is-active' : undefined)}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
