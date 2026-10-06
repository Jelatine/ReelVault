import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { Plugin } from 'vite'

// Generate the exact precache from the completed build, including lazy routes.
export default function offlineBuild(): Plugin {
  let output: string
  return {
    name: 'reelvault-offline',
    apply: 'build',
    configResolved(config) { output = resolve(config.root, config.build.outDir) },
    closeBundle() {
      const files = readdirSync(output, { recursive: true, withFileTypes: true })
        .filter((entry) => entry.isFile())
        .map((entry) => resolve(entry.parentPath, entry.name).slice(output.length))
        .filter((path) => !path.endsWith('.map') && path !== '/sw.js')
        .sort()
      const source = readFileSync(new URL('./worker.js', import.meta.url), 'utf8')
      const hash = createHash('sha256').update(source)
      for (const file of files) hash.update(readFileSync(output + file))
      const worker = source.replace('__SHELL_VERSION__', hash.digest('hex').slice(0, 16))
        .replace('__PRECACHE__', JSON.stringify(files))
      writeFileSync(resolve(output, 'sw.js'), worker)
    },
  }
}
