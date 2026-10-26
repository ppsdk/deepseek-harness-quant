/* Optional browser regression. Requires playwright and an installed Chrome. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const base = process.env.RESEARCH_BASE_URL || 'http://127.0.0.1:8787';
  const output = path.resolve('output/pattern_checks');
  fs.mkdirSync(output, { recursive: true });
  const chrome = process.env.CHROME_EXECUTABLE || path.join(
    process.env.ProgramFiles || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe');
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const configResponse = await page.request.get(base + '/api/research/config');
    assert.equal(configResponse.status(), 200);
    const config = await configResponse.json();
    const presets = config.chart_settings.pattern_presets;
    for (const key of ['gaussian', 'wyckoff', 'dow', 'naked_k', 'vpts', 'kdj', 'rsi_divergence']) {
      assert.ok(presets[key], 'Missing chart preset: ' + key);
    }
    const stockResponse = await page.request.get(base + '/api/studio/stocks?cached=1&limit=1');
    assert.equal(stockResponse.status(), 200);
    const stock = (await stockResponse.json()).stocks[0];
    assert.ok(stock, 'Download at least one stock before running this browser regression.');
    const query = new URLSearchParams({ code: stock.code, start: stock.start, end: stock.end });
    const response = await page.goto(base + '/research?' + query);
    assert.equal(response.status(), 200);
    await page.locator('#stock-chart svg').waitFor({ timeout: 30000 });
    const results = [];
    for (const [key, preset] of Object.entries(presets)) {
      const [chartResponse] = await Promise.all([
        page.waitForResponse(r => r.url().includes('/api/research/chart?') &&
          new URL(r.url()).searchParams.get('indicators') === preset.indicators.join(',')),
        page.locator('#chart-preset').selectOption(key)
      ]);
      assert.equal(chartResponse.status(), 200, key);
      const payload = await chartResponse.json();
      const study = payload.patterns.studies[0];
      assert.ok(study, key + ': study missing');
      await page.waitForFunction(note =>
        document.querySelector('#stock-chart .research-study-note')?.textContent.includes(note), study.note);
      await page.locator('#stock-chart svg').waitFor();
      assert.ok(payload.patterns.events.every(event => event.date >= event.origin_date), key);
      const chart = page.locator('#stock-chart .research-chart-surface');
      const svg = await chart.locator('svg').getAttribute('viewBox');
      assert.ok(svg, key + ': SVG missing');
      const invalid = await chart.locator('svg').evaluate(element =>
        /(?:NaN|undefined|Infinity)/.test(element.outerHTML));
      assert.equal(invalid, false, key + ': invalid SVG coordinate');
      const markers = await chart.locator('.research-pattern-marker').count();
      if (markers) {
        assert.equal(await chart.locator('.research-pattern-marker text').count(), 0);
        await page.locator('#stock-chart [data-action="signal-labels"]').click();
        assert.ok(await chart.locator('.research-pattern-marker text').count() > 0);
        await page.locator('#stock-chart [data-action="signal-labels"]').click();
        assert.equal(await chart.locator('.research-pattern-marker text').count(), 0);
      }
      if (key === 'gaussian') assert.equal(Object.keys(payload.indicators).length, 3);
      if (key === 'kdj') {
        assert.deepEqual([...new Set(Object.values(payload.indicators).map(line => line.panel))], ['kdj']);
      }
      if (key === 'naked_k') assert.equal(Object.keys(payload.indicators).length, 0);
      if (key === 'dow') {
        const panels = new Set(Object.values(payload.indicators).filter(line => !line.overlay).map(line => line.panel));
        assert.equal(panels.size, 3, 'Day, week and month should have separate panels');
      }
      if (key === 'rsi_divergence' && payload.patterns.segments.length) {
        assert.ok(await chart.locator('.research-divergence-guide').count() > 0);
      }
      await chart.screenshot({ path: path.join(output, key + '.png') });
      const before = await page.locator('#stock-chart .research-chart-info').first().textContent();
      await page.locator('#stock-chart [data-action="in"]').click();
      const after = await page.locator('#stock-chart .research-chart-info').first().textContent();
      assert.notEqual(before, after, key + ': zoom did not change the visible range');
      if (payload.patterns.events.length > payload.patterns.page_size) {
        const first = await page.locator('#stock-chart .research-pattern-audit tbody').textContent();
        await page.locator('#stock-chart [data-audit="next"]').click();
        const next = await page.locator('#stock-chart .research-pattern-audit tbody').textContent();
        assert.notEqual(first, next, key + ': signal paging did not change the rows');
      }
      results.push({ preset: key, lines: Object.keys(payload.indicators).length,
        events: payload.patterns.events.length, visibleMarkers: markers,
        divergenceSegments: payload.patterns.segments.length });
    }
    assert.deepEqual(errors, [], 'Browser JavaScript errors');
    fs.writeFileSync(path.join(output, 'ui-summary.json'), JSON.stringify({ ok: true, results }, null, 2));
    console.log(JSON.stringify({ ok: true, results }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
