import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'

// Each run owns a fresh library; never reuse a developer's database or credentials.
const data = mkdtempSync(resolve(tmpdir(), 'reelvault-e2e-'))
const incoming = mkdtempSync(resolve(tmpdir(), 'reelvault-incoming-'))
const backend = resolve('../backend')
if (process.env.REELVAULT_TEST_WHISPER_CACHE) {
  mkdirSync(resolve(data, 'models'), { recursive: true })
  symlinkSync(resolve(process.env.REELVAULT_TEST_WHISPER_CACHE), resolve(data, 'models/whisper'), 'dir')
}
const server = spawn(resolve(backend, '.venv/bin/python'), ['-m', 'reelvault'], {
  cwd: backend,
  stdio: 'inherit',
  env: {
    ...process.env,
    REELVAULT_DATA_DIR: data,
    REELVAULT_IMPORT_DIR: incoming,
    REELVAULT_STATIC_DIR: resolve('dist'),
    REELVAULT_HOST: '127.0.0.1',
    REELVAULT_PORT: '18089',
    REELVAULT_ADMIN_USER: 'e2e-admin',
    REELVAULT_ADMIN_PASSWORD: 'e2e-secret123',
    REELVAULT_UPDATE_CHECK: 'false',
    // Synthetic HTTP fixtures only; production keeps private destinations blocked.
    REELVAULT_LINK_IMPORT_ALLOW_PRIVATE: 'true',
    REELVAULT_ALLOW_SELF_UPDATE: 'false',
  },
})
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => server.kill(signal))
}
server.on('error', (error) => {
  console.error(error)
  rmSync(data, { recursive: true, force: true })
  rmSync(incoming, { recursive: true, force: true })
  process.exit(1)
})
server.on('exit', (code) => {
  rmSync(data, { recursive: true, force: true })
  rmSync(incoming, { recursive: true, force: true })
  process.exit(code ?? 0)
})
