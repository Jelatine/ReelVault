import { Center, Loader } from '@mantine/core'
import { Suspense } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

/** Reset the page boundary so transitions never retain a playing page while loading. */
export default function RouteContent() {
  const { pathname } = useLocation()
  return (
    <Suspense key={pathname} fallback={<Center mih={300} role="status"><Loader /></Center>}>
      <Outlet />
    </Suspense>
  )
}
