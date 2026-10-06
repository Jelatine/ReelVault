import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { NavLink } from 'react-router-dom'
import { IconHome, IconMovie, IconListCheck, IconSettings } from '@tabler/icons-react'

export default function MobileNavigation({ onNavigate }: { onNavigate: () => void }) {
  useTranslation()

  return <nav className="mobile-navigation" aria-label={tr("手机主导航")}>
    {[{ to: '/', label: tr("首页"), icon: IconHome }, { to: '/library', label: tr("视频库"), icon: IconMovie },
      { to: '/jobs', label: tr("任务"), icon: IconListCheck }, { to: '/settings', label: tr("设置"), icon: IconSettings }]
      .map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end onClick={onNavigate}>
        <Icon size={21} aria-hidden="true" /><span>{label}</span>
      </NavLink>)}
  </nav>
}
