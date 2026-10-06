import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { Center, Loader } from '@mantine/core'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import { useAuth } from './lib/auth'
import { useJobEvents } from './lib/jobs'
const CollectionPage = lazy(() => import('./pages/CollectionPage'))
const JobsPage = lazy(() => import('./pages/JobsPage'))
const LibraryPage = lazy(() => import('./pages/LibraryPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const LoginPage = lazy(() => import('./pages/LoginPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const SetupPage = lazy(() => import('./pages/SetupPage'))
const TrashPage = lazy(() => import('./pages/TrashPage'))
const VideoPage = lazy(() => import('./pages/VideoPage'))

function AppRoutes() {
  const { loading, user, setupRequired } = useAuth()
  const location = useLocation()
  useJobEvents(!!user)

  if (loading) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    )
  }
  if (setupRequired) return <SetupPage />
  if (!user) {
    if (location.pathname !== '/login') {
      return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />
    }
    return <LoginPage />
  }

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={location.search ? <Navigate to={`/library${location.search}`} replace /> : <DashboardPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="videos/:id" element={<VideoPage />} />
        <Route path="collections/:id" element={<CollectionPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="trash" element={<TrashPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="login" element={<Navigate to="/" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  useTranslation()
  return <Suspense fallback={<Center mih="100vh"><Loader /></Center>}><AppRoutes /></Suspense>
}
