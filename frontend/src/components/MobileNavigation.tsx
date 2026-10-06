import { NavLink } from 'react-router-dom'
import { IconHome, IconMovie, IconListCheck, IconSettings } from '@tabler/icons-react'

export default function MobileNavigation({ onNavigate }: { onNavigate: () => void }) {
  return <nav className="mobile-navigation" aria-label="手机主导航">
    {[{ to: '/', label: '首页', icon: IconHome }, { to: '/library', label: '视频库', icon: IconMovie },
      { to: '/jobs', label: '任务', icon: IconListCheck }, { to: '/settings', label: '设置', icon: IconSettings }]
      .map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end onClick={onNavigate}>
        <Icon size={21} aria-hidden="true" /><span>{label}</span>
      </NavLink>)}
  </nav>
}
