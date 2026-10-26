const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const { createRequire } = require('node:module')
const { saveKey, configuredTarget } = require('../harness/setup-key.cjs')
const harnessRequire = createRequire(path.resolve(__dirname, '../harness/package.json'))
const { parse } = harnessRequire('yaml')
const fixtureKey = 'sk-compat-fixture-never-a-real-key'

async function withHome(run) {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-credentials-test-'))
  try { await run(home, path.join(home, '.credentials.yaml')) }
  finally {
    assert.equal(path.dirname(path.resolve(home)), path.resolve(os.tmpdir()))
    assert.ok(path.basename(home).startsWith('dsh-credentials-test-'))
    await fs.rm(home, { recursive: true, force: true })
  }
}

test('creates the rc.2 versioned credential layout', () => withHome(async (home, file) => {
  await saveKey(home, fixtureKey)
  assert.deepEqual(parse(await fs.readFile(file, 'utf8')), { version: 1, refs: { DEEPSEEK_API_KEY: fixtureKey } })
}))

test('retains browser grant records, other references, and comments', () => withHome(async (home, file) => {
  const records = { 'client-connection/browser-session': { kind: 'grant', payload: { version: 1, secret: 'fixture' } } }
  await fs.writeFile(file, '# keep this comment\n' + JSON.stringify({ version: 1, refs: { OTHER_KEY: 'keep' }, records }))
  await saveKey(home, fixtureKey)
  const text = await fs.readFile(file, 'utf8')
  const actual = parse(text)
  assert.deepEqual(actual.records, records)
  assert.equal(actual.refs.OTHER_KEY, 'keep')
  assert.equal(actual.refs.DEEPSEEK_API_KEY, fixtureKey)
  assert.match(text, /keep this comment/)
}))

test('migrates legacy flat credentials without dropping other keys', () => withHome(async (home, file) => {
  await fs.writeFile(file, 'OTHER_KEY: keep\nDEEPSEEK_API_KEY: old-value\n')
  await saveKey(home, fixtureKey)
  const actual = parse(await fs.readFile(file, 'utf8'))
  assert.deepEqual(actual, { version: 1, refs: { OTHER_KEY: 'keep', DEEPSEEK_API_KEY: fixtureKey } })
}))

test('preserves an invalid or unsupported document and rejects invalid keys', () => withHome(async (home, file) => {
  for (const original of ['refs: [bad\n', 'version: 2\n', 'version: 1\nrefs: []\n']) {
    await fs.writeFile(file, original)
    await assert.rejects(saveKey(home, fixtureKey))
    assert.equal(await fs.readFile(file, 'utf8'), original)
  }
  await assert.rejects(saveKey(home, 'not-a-key'))
}))

test('DataEyes key preserves the official key and grant records', () => withHome(async (home, file) => {
  const original = { version: 1, refs: { DEEPSEEK_API_KEY: 'official-fixture-to-retain' },
    records: { 'client-connection/browser-session': { kind: 'grant', payload: { secret: 'fixture' } } } }
  await fs.writeFile(file, JSON.stringify(original))
  await saveKey(home, 'dataeyes-fixture-key-not-a-secret', 'DATAEYES_API_KEY')
  const actual = parse(await fs.readFile(file, 'utf8'))
  assert.equal(actual.refs.DEEPSEEK_API_KEY, original.refs.DEEPSEEK_API_KEY)
  assert.equal(actual.refs.DATAEYES_API_KEY, 'dataeyes-fixture-key-not-a-secret')
  assert.deepEqual(actual.records, original.records)
}))

test('setup chooses the selected provider reference from the profile', () => withHome(async (home) => {
  const profile = path.join(home, 'profiles', 'web')
  await fs.mkdir(profile, { recursive: true })
  await fs.writeFile(path.join(profile, 'cordis.patch.yml'), JSON.stringify([
    { id: 'agent-default-model', config: { provider: 'dataeyes' } },
    { id: 'llm-pi-ai', config: { providers: { dataeyes: { apiKeyEnv: 'DATAEYES_API_KEY', displayName: 'DataEyes' } } } }
  ]))
  assert.deepEqual(await configuredTarget(home), { reference: 'DATAEYES_API_KEY', label: 'DataEyes', provider: 'dataeyes' })
}))

test('unknown selected provider never silently writes the official credential', () => withHome(async (home) => {
  const profile = path.join(home, 'profiles', 'web')
  await fs.mkdir(profile, { recursive: true })
  await fs.writeFile(path.join(profile, 'cordis.patch.yml'), JSON.stringify([
    { id: 'agent-default-model', config: { provider: 'missing-route' } }
  ]))
  await assert.rejects(configuredTarget(home), /no API key reference/)
  await assert.rejects(saveKey(home, fixtureKey, 'BAD REFERENCE'))
}))
