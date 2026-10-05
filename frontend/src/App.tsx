import { Center, Loader } from '@mantine/core'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import { useAuth } from './lib/auth'
import { useJobEvents } from './lib/jobs'
import JobsPage from './pages/JobsPage'
import LibraryPage from './pages/LibraryPage'
import LoginPage from './pages/LoginPage'
import SettingsPage from './pages/SettingsPage'
import SetupPage from './pages/SetupPage'
import TrashPage from './pages/TrashPage'
import VideoPage from './pages/VideoPage'

export default function App() {
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
        <Route index element={<LibraryPage />} />
        <Route path="videos/:id" element={<VideoPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="trash" element={<TrashPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="login" element={<Navigate to="/" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
