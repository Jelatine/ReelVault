import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { lazy, useEffect, type ReactNode } from 'react'
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import RouteContent from './RouteContent'

vi.mock('@mantine/core', () => ({
  Center: ({ children, role }: { children: ReactNode; role?: string }) => <div role={role}>{children}</div>,
  Loader: () => <span>Loading page</span>,
}))

afterEach(cleanup)

test('leaves the playing page before a lazy destination finishes loading', async () => {
  let finishLoading!: (value: { default: () => ReactNode }) => void
  const Destination = lazy(() => new Promise<{ default: () => ReactNode }>((resolve) => { finishLoading = resolve }))
  const stopPlayback = vi.fn()
  function PlayingPage() {
    useEffect(() => stopPlayback, [])
    return <div>Playing video</div>
  }
  function Layout() {
    const { pathname } = useLocation()
    return <>
      <nav><Link to="/library">Library</Link><Link to="/settings">Settings</Link></nav>
      <div data-testid="location">{pathname}</div>
      <RouteContent />
    </>
  }
  render(<MemoryRouter initialEntries={['/videos/one']}>
    <Routes>
      <Route element={<Layout />}>
        <Route path="videos/:id" element={<PlayingPage />} />
        <Route path="library" element={<Destination />} />
        <Route path="settings" element={<div>Settings page</div>} />
      </Route>
    </Routes>
  </MemoryRouter>)
  expect(screen.getByText('Playing video')).toBeTruthy()
  fireEvent.click(screen.getByRole('link', { name: 'Library' }))
  await act(async () => {})
  expect(screen.getByTestId('location').textContent).toBe('/library')
  expect(screen.queryByText('Playing video')).toBeNull()
  expect(stopPlayback).toHaveBeenCalledOnce()
  expect(screen.getByRole('status')).toBeTruthy()

  // Navigation remains usable even if the requested chunk has not arrived.
  fireEvent.click(screen.getByRole('link', { name: 'Settings' }))
  expect(await screen.findByText('Settings page')).toBeTruthy()
  await act(async () => { finishLoading({ default: () => <div>Library page</div> }) })
  expect(screen.getByText('Settings page')).toBeTruthy()
  expect(screen.queryByText('Library page')).toBeNull()
  fireEvent.click(screen.getByRole('link', { name: 'Library' }))
  expect(await screen.findByText('Library page')).toBeTruthy()
})
