// 量化系统 ↔ DeepSeek HARNESS 0.2.1-rc.2 桥接插件
// 来源：dsqq-1 动态插件 v13（牛散 persona 接入 + 会话/聊天桥接）
// 部署：由 DSH_HOME/profiles/web/cordis.patch.yml 挂载为宿主组合插件行
// inject：声明硬依赖，cordis 在 webServer 等服务就绪后才 apply（避免提前 return 导致路由不注册）
const fs = require('node:fs')
const path = require('node:path')
const { randomUUID } = require('node:crypto')
const projectRoot = path.resolve(__dirname, '../../../../../')

module.exports = {
  inject: ['webServer', 'sessions', 'agents', 'subagents', 'sessionTitle', 'sessionPersistence', 'systemPrompt', 'tools', 'agentDefaultModel'],
  async apply(ctx) {
    const { createUserMessage } = await import('@deepseek-ai/dsh-llm')
    const { Session } = await import('@deepseek-ai/dsh-session')
    const webServer = ctx.get('webServer')
    const sessions = ctx.get('sessions')
    const agents = ctx.get('agents')
    const sessionTitle = ctx.get('sessionTitle')
    const subagents = ctx.get('subagents')
    const sessionPersistence = ctx.get('sessionPersistence')
    if (webServer === undefined) return

    // rc.2 identifies every queued message and no longer creates a root on web boot.
    let rootBoot
    const ownedRoots = []
    async function ensureRoot() {
      const current = defaultAgentId()
      if (current) return current
      if (!rootBoot) rootBoot = (async () => {
        const stored = await sessionPersistence.list()
        const previous = stored.find(row => !row.header.parentSession && row.header.cwd === projectRoot)
        const agentOptions = ctx.get('agentDefaultModel').currentSelection()
        const handle = previous
          ? await agents.resume({ resumeSessionId: previous.header.id, agentOptions })
          : await agents.create({ sessionId: randomUUID(), meta: { cwd: projectRoot }, agentOptions })
        ownedRoots.push(handle)
        return String(handle.agent.id)
      })().finally(() => { rootBoot = null })
      return rootBoot
    }
    ctx.effect(() => async () => {
      await Promise.all(ownedRoots.map(handle => handle.dispose()))
    })

    function cors(res) {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type')
      res.setHeader('Access-Control-Max-Age', '600')
    }
    function json(res, code, obj) {
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' })
      res.end(JSON.stringify(obj))
    }
    async function readBody(req) {
      let body = ''
      for await (const chunk of req) body += new TextDecoder().decode(chunk)
      return body
    }
    function parseQuery(url) {
      const out = {}
      const qs = String(url || '').split('?')[1]
      if (!qs) return out
      for (const pair of qs.split('&')) {
        const i = pair.indexOf('=')
        if (i < 0) continue
        try { out[decodeURIComponent(pair.slice(0, i))] = decodeURIComponent(pair.slice(i + 1)) } catch (e) {}
      }
      return out
    }
    function defaultAgentId() {
      if (!agents) return null
      const roots = agents.roots()
      return roots[0] ? String(roots[0].id) : null
    }
    function resolveAgent(sessionId) {
      if (!agents) return null
      const id = sessionId || defaultAgentId()
      if (!id) return null
      return agents.get(id) || agents.roots().find(function (a) { return String(a.id) === id }) || null
    }
    function resolveSession(id) {
      if (sessions) {
        const s = sessions.get(id)
        if (s) return s
      }
      const agent = resolveAgent(id)
      if (agent && agent.session) return agent.session
      return null
    }
    function textOfContent(content) {
      if (typeof content === 'string') return content
      if (!Array.isArray(content)) return ''
      return content.map(function (p) {
        if (p == null) return ''
        if (typeof p === 'string') return p
        if (p.type === 'text') return String(p.text || '')
        return ''
      }).filter(Boolean).join('\n')
    }
    function isNoise(text) {
      const t = String(text || '').trim()
      if (!t) return true
      if (t.indexOf('Current runtime context. This snapshot supersedes') === 0) return true
      if (t.indexOf('【任务】你是「') === 0) return true
      return false
    }
    function extractMessages(eventsArr, limit) {
      const out = []
      const arr = Array.isArray(eventsArr) ? eventsArr : []
      for (let i = arr.length - 1; i >= 0 && out.length < (limit || 20); i--) {
        const ev = arr[i]
        if (!ev) continue
        let role = null, content = null, ts = null
        if (ev.type === 'user/message' || ev.type === 'assistant/message') {
          const d = ev.data || {}
          const m = d.message && d.message.role ? d.message : d
          role = ev.type === 'user/message' ? 'user' : 'assistant'
          content = m.content
          ts = ev.time != null ? ev.time : (d.ts != null ? d.ts : null)
        } else {
          const msg = ev.message || ev
          role = msg && msg.role
          content = msg && msg.content
          ts = (ev.ts != null ? ev.ts : (msg && msg.ts != null ? msg.ts : null))
        }
        if (role !== 'user' && role !== 'assistant') continue
        const text = textOfContent(content)
        if (isNoise(text)) continue
        out.unshift({ role: role, text: text.slice(0, 4000), ts: ts })
      }
      return out
    }
    function liveMessages(session, limit) {
      let messages = []
      if (typeof session.deriveMessages === 'function') messages = session.deriveMessages()
      else if (session.events) messages = session.events
      return extractMessages(messages, limit)
    }
    async function coldMessages(id, limit) {
      const debug = { tried: false, events: 0, error: null }
      if (!sessionPersistence || typeof sessionPersistence.open !== 'function') {
        debug.error = 'no sessionPersistence'
        return { messages: null, debug: debug }
      }
      try {
        debug.tried = true
        const handle = await sessionPersistence.open(id, 'read')
        let r
        try {
          r = await handle.read()
          const restored = Session.fromRestore(id, r.events, handle.header, handle.inheritedEventCount, r.eventState)
          r = { events: restored.deriveMessages() }
        } finally {
          await handle.close()
        }
        if (r && Array.isArray(r.events)) {
          debug.events = r.events.length
          return { messages: extractMessages(r.events, limit), debug: debug }
        }
        debug.error = 'no events array'
        return { messages: null, debug: debug }
      } catch (e) {
        debug.error = String((e && e.message) || e).slice(0, 200)
        return { messages: null, debug: debug }
      }
    }
    async function messagesOf(id, limit) {
      const session = resolveSession(id)
      if (session) {
        const live = liveMessages(session, limit)
        if (live.length) return { messages: live, debug: { live: true, count: live.length } }
      }
      const c = await coldMessages(id, limit)
      return { messages: c.messages || [], debug: c.debug }
    }
    function recentTextOf(list) {
      for (let i = list.length - 1; i >= 0; i--) {
        if (list[i] && list[i].text) return { text: list[i].text.slice(0, 120), role: list[i].role }
      }
      return null
    }
    function sessionTitleOf(id) {
      try {
        const session = resolveSession(id)
        if (!session) return null
        if (sessionTitle && typeof sessionTitle.get === 'function') {
          const st = sessionTitle.get(session)
          if (st && st.title) return st.title
        }
        const meta = session.meta || session.header || {}
        if (meta.title) return meta.title
        const cwd = meta.cwd
        if (cwd) {
          const parts = String(cwd).replace(/\\/g, '/').split('/').filter(Boolean)
          return parts[parts.length - 1] || null
        }
      } catch (e) {}
      return null
    }

    function buildPersona(p) {
      const researchFile = path.join(projectRoot, 'config', 'niu_research.json')
      const researchSource = fs.existsSync(researchFile) ? researchFile : researchFile + '.example'
      const research = fs.existsSync(researchSource) ? JSON.parse(fs.readFileSync(researchSource, 'utf8')) : { scope: '', evidence: '' }
      return '你是「牛散·' + p.name + '」人格模拟（基于公开资料牛散档案 ' + p.skill + ' 蒸馏魔改；不构成投资建议）。\n'
        + research.scope + '\n' + research.evidence + '\n'
        + '【风格与选股视角】\n' + p.style + '\n'
        + '【你的职责】主观选股顾问：基于量化系统提供的【Pitch 候选卡】与【当前持仓】做选股决策。Pitch 卡字段含义：score=综合评分(0-100)、winrate_est=历史胜率、upside_est=预期空间、evidence=证据链、stop_plan=止损/离场计划、factors=关键因子值、risk_flags=风险标记。\n'
        + '【铁律】\n'
        + '1. 只在你拿到的 Pitch 候选卡与当前持仓范围内决策；卡外股票最多建议"关注"，绝不推荐买入。\n'
        + '2. 讨论持仓时核对对话提供的持仓、止损与退出条件；资料不足时说明缺口，不虚构仓位或成交。\n'
        + '3. 逐卡输出：代码名称 → 你的判断（高/中/低优先级）→ 核心理由（关联你的方法论）→ 与量化信号冲突点。结论先行，精炼列表，≤400 字。\n'
        + '4. 批判自省：你是人格模拟，风格来自公开资料，存在信息滞后/幸存者偏差/造神风险；不追高、不造神。结尾注明"不构成投资建议"。\n'
        + '5. 决策结构化输出（必须遵守）：在回复【最后一行】输出一个纯 JSON 对象（不要 markdown 代码块），格式：{"niu_decisions":[{"code":"600519.SH","action":"buy","priority":"high","reason_short":"垄断成瘾质量"}]}。action 限 buy/hold/sell/watch；priority 限 high/mid/low；reason_short ≤15 字；只包含你在 Pitch 卡与当前持仓范围内明确给出的决策；本轮没有明确决策就输出 {"niu_decisions":[]}。\n'
        + '【约束】不调用任何工具、不读取文件、不联网——只基于对话中给出的数据作答。'
    }
    function personas() {
      const config = path.join(projectRoot, 'config', 'niu_personas.json')
      const source = fs.existsSync(config) ? config : config + '.example'
      if (!fs.existsSync(source)) return {}
      try {
        const rows = JSON.parse(fs.readFileSync(source, 'utf8'))
        if (!rows || typeof rows !== 'object' || Array.isArray(rows)) throw new Error('invalid persona catalog')
        for (const [key, row] of Object.entries(rows)) {
          if (!row || ['name', 'tag', 'skill', 'style'].some(field => typeof row[field] !== 'string')) {
            throw new Error('invalid persona: ' + key)
          }
        }
        return rows
      } catch (error) {
        console.error('DSHQuant persona config unavailable: ' + error.message)
        return {}
      }
    }

    const personaChild = {}
    const personaSnap = {}
    const niuBootLock = {}

    const personaByChild = new Map()
    ctx.on('agent/created', ({ agent }) => {
      // Native GUI resume can precede any Quant API discovery after a restart.
      const projections = ctx.get('sessionProjections')
      if (!projections) throw new Error('sessionProjections is required for persona restoration')
      const identity = projections.snapshot(agent.session, ['subagent']).values.subagent
      const label = identity && agent.session.isOwnSeq(identity.seq) ? identity.label || '' : ''
      const key = personaByChild.get(String(agent.id)) || (label.startsWith('牛散·') ? label.slice(3) : null)
      if (!key) return
      const persona = personas()[key]
      if (!persona) throw new Error('persona removed: ' + key)
      agent.ctx.systemPrompt.section({
        name: 'dsq:persona', order: 0, complete: true, interpolate: false,
        text: buildPersona(persona)
      })
      agent.ctx.tools.restrict({ allow: [] })
      agent.ctx.tools.guard(() => '量化人格仅允许对话，不允许执行工具')
    })
    function requestSignal() {
      return new AbortController().signal
    }
    async function discoverNiuChildren() {
      const out = {}
      if (!subagents || !agents) return out
      const root = defaultAgentId()
      if (!root) return out
      let entries = []
      try { entries = await subagents.listChildren(root) } catch (e) { entries = [] }
      for (const e of entries) {
        if (!e || e.mode !== 'continuable') continue
        const id = e.id ? String(e.id) : null
        const label = e.label || ''
        if (!id || label.indexOf('牛散·') !== 0) continue
        out[label.slice(3)] = id
        personaByChild.set(id, label.slice(3))
      }
      return out
    }
    function ensureNiuChild(key, snapshot, text) {
      if (personaChild[key]) return Promise.resolve({ childId: personaChild[key], created: false })
      if (niuBootLock[key]) return niuBootLock[key].then(out => ({ ...out, created: false }))
      niuBootLock[key] = (async function () {
        try {
          await ensureRoot()
          const found = await discoverNiuChildren()
          if (found[key]) {
            personaChild[key] = found[key]
            return { childId: found[key], created: false }
          }
          const rootAgent = resolveAgent()
          const childId = randomUUID()
          personaByChild.set(childId, key)
          const content = text + (snapshot ? '\n\n【量化 Pitch 快照（最新）】\n' + snapshot : '')
          const activation = await subagents.startActivation({
            provider: 'spawn', label: '牛散·' + key, childId,
            request: { prompt: [{ type: 'text', text: content }], parent: rootAgent },
            signal: requestSignal(), delivery: 'caller'
          })
          activation.result.catch(error => console.error('niu: ' + String(error.message || error).slice(0, 200)))
          personaChild[key] = String(activation.childId)
          if (snapshot) personaSnap[key] = snapshot
          return { childId: personaChild[key], created: true }
        } finally {
          delete niuBootLock[key]
        }
      })()
      return niuBootLock[key]
    }

    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/sessions',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        try {
          await ensureRoot()
          const roots = agents ? agents.roots() : []
          const list = []
          for (let idx = 0; idx < roots.length; idx++) {
            const a = roots[idx]
            const id = a && a.id ? String(a.id) : null
            if (!id) continue
            const msgs = await messagesOf(id, 3)
            const last = recentTextOf(msgs.messages)
            list.push({
              id: id,
              title: sessionTitleOf(id) || ('会话 ' + (idx + 1)),
              preview: last ? last.text : '',
              role: last ? last.role : '',
              ts: Date.now(),
              current: idx === 0
            })
          }
          json(res, 200, { ok: true, sessions: list })
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 200) }) }
      }
    }))

    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/niu/sessions',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        try {
          const found = await discoverNiuChildren()
          const catalog = []
          const rows = personas()
          for (const k of Object.keys(rows)) {
            const p = rows[k]
            const childId = personaChild[k] || found[k] || null
            let preview = ''
            if (childId) {
              const msgs = await messagesOf(childId, 3)
              const last = recentTextOf(msgs.messages)
              preview = last ? last.text : ''
            }
            catalog.push({ id: k, name: p.name, tag: p.tag, skill: p.skill, childId: childId, preview: preview })
          }
          json(res, 200, { ok: true, personas: catalog })
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 200) }) }
      }
    }))

    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/niu/chat',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        let key = '', text = '', snapshot = ''
        try {
          const payload = JSON.parse((await readBody(req)) || '{}')
          key = String(payload.persona || '').trim()
          text = String(payload.text || '').trim()
          if (payload.snapshot) snapshot = String(payload.snapshot)
        } catch (e) { json(res, 400, { ok: false, error: 'bad json' }); return }
        const p = personas()[key]
        if (!p) { json(res, 404, { ok: false, error: 'unknown persona: ' + key }); return }
        if (!text) { json(res, 400, { ok: false, error: 'empty text' }); return }
        try {
          const boot = await ensureNiuChild(key, snapshot || personaSnap[key] || '', text)
          const childId = boot.childId
          if (!childId) { json(res, 500, { ok: false, error: '牛散子代理创建失败（subagents 不可用？）', persona: key }); return }
          const rootId = defaultAgentId()
          const rootAgent = rootId ? agents.get(rootId) : null
          if (!rootAgent || !subagents) { json(res, 500, { ok: false, error: 'agents/subagents 不可用' }); return }
          if (!boot.created) {
            if (snapshot) personaSnap[key] = snapshot
            const content = text + (personaSnap[key] ? '\n\n【量化 Pitch 快照（最新）】\n' + personaSnap[key] : '')
            await subagents.prompt({
              requestId: randomUUID(), parentSessionId: rootId, childSessionId: childId,
              mode: 'continuable', delivery: 'queue', content: [{ type: 'text', text: content }]
            }, requestSignal())
          }
          json(res, 200, { ok: true, accepted: true, persona: key, childId: childId })
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 200) }) }
      }
    }))

    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/niu/recent',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        try {
          const q = parseQuery(req.url)
          const key = String(q.persona || '').trim()
          if (!personas()[key]) { json(res, 404, { ok: false, error: 'unknown persona: ' + key }); return }
          let childId = personaChild[key]
          if (!childId) {
            const found = await discoverNiuChildren()
            childId = found[key] || null
            if (childId) personaChild[key] = childId
          }
          if (!childId) { json(res, 200, { ok: true, persona: key, childId: null, messages: [], debug: { child: null } }); return }
          const limit = Math.max(1, Math.min(50, parseInt(q.limit || '20', 10) || 20))
          const r = await messagesOf(childId, limit)
          json(res, 200, { ok: true, persona: key, childId: childId, messages: r.messages, debug: r.debug })
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 200) }) }
      }
    }))

    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/chat2',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        let text = '', sessionId = null
        try {
          const payload = JSON.parse((await readBody(req)) || '{}')
          text = String(payload.text || '').trim()
          if (payload.sessionId) sessionId = String(payload.sessionId)
        } catch (e) { json(res, 400, { ok: false, error: 'bad json' }); return }
        if (!text) { json(res, 400, { ok: false, error: 'empty text' }); return }
        try {
          if (!sessionId) await ensureRoot()
          const agent = resolveAgent(sessionId)
          if (!agent) { json(res, 404, { ok: false, error: 'no agent', sessionId: sessionId || defaultAgentId() }); return }
          const message = createUserMessage({ content: [{ type: 'text', text }], source: { kind: 'user' } })
          if (typeof agent.followup === 'function') agent.followup(message)
          else if (typeof agent.steer === 'function') agent.steer(message)
          else { json(res, 500, { ok: false, error: 'no followup/steer' }); return }
          json(res, 200, { ok: true, accepted: true, sessionId: String(agent.id) })
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 200) }) }
      }
    }))

    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/recent',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        try {
          const q = parseQuery(req.url)
          const sessionId = q.sessionId || defaultAgentId()
          const limit = Math.max(1, Math.min(50, parseInt(q.limit || '20', 10) || 20))
          const r = await messagesOf(sessionId, limit)
          json(res, 200, { ok: true, sessionId: sessionId, messages: r.messages })
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 200) }) }
      }
    }))

    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/agents',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        try {
          const roots = agents ? agents.roots() : []
          json(res, 200, { ok: true, rootIds: roots.map(function (a) { return a && a.id ? String(a.id) : null }).filter(Boolean), ts: Date.now() })
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 200) }) }
      }
    }))

    // ── 量化数据读取端点（供 DeepSeek HARNESS 工具调用）──────────────────────
    // GET /quant/data?type=<kind>&...
    // kind: factors | experiments | pools | signals | timing | screener
    ctx.effect(() => webServer.register({
      kind: 'exact', path: '/quant/data',
      handler: async (req, res) => {
        cors(res)
        if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }
        if (req.method !== 'GET') { json(res, 405, { ok: false, error: 'GET only' }); return }
        const q = parseQuery(req.url)
        const type = String(q.type || '').trim()
        try {
          // Proxy request to the Python research API on port 8787
          const http = require('node:http')
          const routes = {
            factors:     '/api/studio/config',
            experiments: '/api/studio/experiments',
            pools:       '/api/studio/pools',
            signals:     '/api/studio/signals',
            timing:      '/api/studio/timing?index=000300.SH&rule=none',
            market:      '/api/studio/market/analysis',
            dual:        '/api/studio/dual',
            stock:       '/api/studio/stock/profile?code=' + encodeURIComponent(q.code || ''),
            screener:    '/api/studio/pools/screen?' + Object.entries(q).filter(function(kv){return kv[0]!=='type';}).map(function(kv){return encodeURIComponent(kv[0])+'='+encodeURIComponent(kv[1]);}).join('&'),
          }
          const apiPath = routes[type]
          if (!apiPath) {
            json(res, 400, { ok: false, error: 'type must be one of: ' + Object.keys(routes).join(', ') }); return
          }
          const data = await new Promise(function(resolve, reject) {
            const proxyReq = http.get({ hostname: '127.0.0.1', port: 8787, path: apiPath }, function(proxyRes) {
              let body = ''
              proxyRes.on('data', function(chunk) { body += chunk })
              proxyRes.on('end', function() {
                try { resolve(JSON.parse(body)) } catch(e) { reject(new Error('invalid JSON from research API')) }
              })
            })
            proxyReq.on('error', reject)
            proxyReq.setTimeout(15000, function() { proxyReq.destroy(new Error('timeout')) })
          })
          // Summarise large payloads to keep HARNESS context manageable
          if (type === 'factors' && data && data.factor_catalog) {
            const cats = {}
            Object.keys(data.factor_catalog).forEach(function(k) {
              const f = data.factor_catalog[k]
              const cat = f.category || '其他'
              cats[cat] = cats[cat] || []
              cats[cat].push({ id: k, label: f.label, available: f.available })
            })
            json(res, 200, { ok: true, type: 'factors', summary: cats, strategies: Object.keys(data.strategies || {}), default_strategy: data.default_strategy })
            return
          }
          if (type === 'experiments' && data && data.runs) {
            const runs = (data.runs || []).slice(0, 20).map(function(r) {
              return { id: r.id, title: r.title, strategy: r.strategy, verdict: r.verdict,
                       annual_return: r.annual_return, sharpe: r.sharpe, max_drawdown: r.max_drawdown,
                       generated_at: r.generated_at, factors: (r.factors || []).slice(0, 8) }
            })
            json(res, 200, { ok: true, type: 'experiments', total: data.runs.length, runs: runs })
            return
          }
          json(res, 200, Object.assign({ type: type }, data))
        } catch (e) { json(res, 500, { ok: false, error: String((e && e.message) || e).slice(0, 300) }) }
      }
    }))
  },
}
