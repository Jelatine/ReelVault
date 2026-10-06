import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse } from '@babel/parser'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = name => JSON.parse(fs.readFileSync(path.join(root, 'src/locales', name), 'utf8'))
const zh = read('zh.json'), en = read('en.json'), errors = read('api-errors.json')
const player = read('player.json')
const issues = []
const placeholders = text => [...text.matchAll(/{{\s*([^{}]+?)\s*}}/g)].map(match => match[1]).sort().join('|')
for (const [key, value] of Object.entries(zh)) {
  if (!en[key]) issues.push(`Missing English: ${key}`)
  else if (placeholders(value) !== placeholders(en[key])) issues.push(`Parameter mismatch: ${key}`)
}
for (const key of Object.keys(en)) if (!(key in zh)) issues.push(`Missing Chinese: ${key}`)
for (const [key, value] of Object.entries({ ...errors, ...player })) {
  if (!value.zh || !value.en || placeholders(value.zh) !== placeholders(value.en)) issues.push(`Invalid API translation: ${key}`)
}
function scan(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name)
    if (entry.isDirectory()) { scan(file); continue }
    if (!/\.(tsx?|jsx?)$/.test(file) || /\.test\./.test(file)) continue
    const tree = parse(fs.readFileSync(file, 'utf8'), { sourceType: 'module', plugins: ['typescript', 'jsx'] })
    function walk(node, parent, inFunction = false) {
      if (!node || typeof node !== 'object') return
      const insideFunction = inFunction || /Function|Method/.test(node.type ?? '')
      if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'tr') {
        if (!insideFunction) issues.push(`${path.relative(root, file)}:${node.loc.start.line}: translation frozen at module initialization`)
        const key = node.arguments[0]
        if (key?.type === 'StringLiteral' && !(key.value in zh)) issues.push(`${path.relative(root, file)}:${key.loc.start.line}: missing key ${key.value}`)
      }
      if (['StringLiteral', 'JSXText', 'TemplateElement'].includes(node.type)) {
        const text = node.type === 'TemplateElement' ? node.value.raw : node.value
        const translationKey = parent?.type === 'CallExpression' && parent.callee.type === 'Identifier' && parent.callee.name === 'tr' && parent.arguments[0] === node
        const autonym = path.basename(file) === 'LanguageSelect.tsx' && text === '中文'
        if (/[\u3400-\u9fff\u3000-\u303f\uff01-\uff60]/.test(text) && !translationKey && !autonym) issues.push(`${path.relative(root, file)}:${node.loc.start.line}: untranslated ${text.trim()}`)
      }
      for (const [key, child] of Object.entries(node)) {
        if (['loc', 'start', 'end', 'comments', 'leadingComments', 'trailingComments', 'extra'].includes(key)) continue
        if (Array.isArray(child)) child.forEach(item => walk(item, node, insideFunction))
        else if (child && typeof child === 'object') walk(child, node, insideFunction)
      }
    }
    walk(tree)
  }
}
scan(path.join(root, 'src'))
if (issues.length) { console.error(issues.join('\n')); process.exitCode = 1 }
else console.log(`Verified ${Object.keys(zh).length} UI, ${Object.keys(errors).length} API error and ${Object.keys(player).length} player translations.`)
