// DSH rc.2 credential setup: retain browser grants and unrelated credentials.
const fs = require('node:fs/promises')
const path = require('node:path')
const { createRequire } = require('node:module')
const { createInterface } = require('node:readline/promises')
const { Writable } = require('node:stream')
const credentialRequire = createRequire(require.resolve('@deepseek-ai/dsh-credentials-local/package.json'))
const { parseDocument, isMap } = credentialRequire('yaml')

async function saveKey(home, key, reference = 'DEEPSEEK_API_KEY') {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(reference)) throw new Error('Invalid credential reference.')
  if (typeof key !== 'string' || key.length < 20 || /\s|[<>]/.test(key)) throw new Error('Invalid API key format.')
  if (reference === 'DEEPSEEK_API_KEY' && !/^sk-[A-Za-z0-9_-]{17,}$/.test(key)) throw new Error('Invalid DeepSeek API key format (expected sk-...).')
  const { withFileLock, writeFileAtomic } = await import('@deepseek-ai/dsh-atomic-write')
  const file = path.join(home, '.credentials.yaml')
  await fs.mkdir(home, { recursive: true })
  await withFileLock(file, async () => {
    let text = ''
    try { text = await fs.readFile(file, 'utf8') } catch (error) { if (error.code !== 'ENOENT') throw error }
    const doc = parseDocument(text)
    if (doc.errors.length) throw new Error('Cannot parse credentials; existing file was preserved.')
    const value = doc.toJS() || {}
    if (typeof value !== 'object' || Array.isArray(value)) throw new Error('Credentials must be a mapping.')
    if (Object.keys(value).length && value.version === undefined) {
      if (Object.entries(value).some(([k, v]) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(k) || typeof v !== 'string' || !v)) {
        throw new Error('Unrecognized legacy credentials; existing file was preserved.')
      }
      doc.contents = doc.createNode({ version: 1, refs: value })
    } else if (value.version !== undefined && value.version !== 1) {
      throw new Error('Unsupported credential version; existing file was preserved.')
    }
    if (doc.has('refs') && !isMap(doc.get('refs'))) throw new Error('Credential refs must be a mapping.')
    doc.set('version', 1)
    doc.setIn(['refs', reference], key)
    await writeFileAtomic(file, doc.toString(), { mode: 0o600, dirMode: 0o700 })
  })
}

async function configuredTarget(home) {
  const file = path.join(home, 'profiles', 'web', 'cordis.patch.yml')
  let rows
  try { rows = parseDocument(await fs.readFile(file, 'utf8')).toJS() } catch (error) {
    if (error.code === 'ENOENT') return { reference: 'DEEPSEEK_API_KEY', label: 'DeepSeek', provider: 'deepseek-official' }
    throw new Error('Cannot read the model route configuration.')
  }
  if (!Array.isArray(rows)) throw new Error('The model route configuration must be a list.')
  const provider = rows.find(row => row.id === 'agent-default-model')?.config?.provider
  const route = rows.find(row => row.id === 'llm-pi-ai')?.config?.providers?.[provider]
  if (route?.apiKeyEnv) return { reference: route.apiKeyEnv, label: route.displayName || provider, provider }
  if (!provider || provider === 'deepseek-official') {
    return { reference: 'DEEPSEEK_API_KEY', label: 'DeepSeek', provider: 'deepseek-official' }
  }
  throw new Error('The selected provider has no API key reference; configure apiKeyEnv first.')
}

async function main() {
  const home = process.argv.slice(2).find(arg => arg !== '--stdin') || path.join(__dirname, 'home')
  const target = await configuredTarget(home)
  let key
  if (process.argv.includes('--stdin')) {
    let input = ''
    for await (const chunk of process.stdin) input += chunk
    key = input.trim()
  } else {
    process.stdout.write(target.label + ' API Key (input hidden): ')
    const sink = new Writable({ write(chunk, encoding, callback) { callback() } })
    const prompt = createInterface({ input: process.stdin, output: sink, terminal: true })
    try { key = (await prompt.question('')).trim() } finally { prompt.close(); process.stdout.write('\n') }
  }
  await saveKey(home, key, target.reference)
  console.log('[OK] ' + target.label + ' API key saved. Other credentials and browser grants preserved.')
  console.log('Next: python launcher.py')
}

module.exports = { saveKey, configuredTarget }
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1 })
