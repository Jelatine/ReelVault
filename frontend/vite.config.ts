import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import offlineBuild from './pwa/plugin.ts'

const backend = process.env.REELVAULT_BACKEND ?? 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react(), offlineBuild()],
  server: {
    proxy: {
      '/api': { target: backend, changeOrigin: false },
      '/healthz': backend,
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}', 'pwa/**/*.test.ts'],
  },
})
