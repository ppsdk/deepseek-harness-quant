/* Focused Chrome regression for the consolidated research chart controls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {chromium} = require('playwright');

async function main() {
  const base = process.env.RESEARCH_BASE_URL || 'http://127.0.0.1:8787';
  const out = path.resolve('output/chart_controls');
  fs.mkdirSync(out, {recursive: true});
  const html = fs.readFileSync('ui_v2/pages/research.html', 'utf8');
  for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(script[1]);
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_EXECUTABLE || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true
  });
  const checks = [], errors = [];
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.on('pageerror', e => errors.push(e.message));
    const response = await page.goto(base + '/research');
    assert.equal(response.status(), 200);
    await page.waitForFunction(() => !document.querySelector('#chart-load').disabled);
    await page.evaluate(() => localStorage.removeItem('research.chart.prefs'));
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('#chart-load').disabled);
    const configResponse = await page.request.get(base + '/api/research/config');
    assert.equal(configResponse.status(), 200);
    const config = await configResponse.json();
    const settings = config.chart_settings;
    const catalog = config.factor_catalog;
    assert.equal(await page.locator('#chart-config-panel').isVisible(), false);
    const canonical = id => settings.indicator_families[catalog[id].operation]?.base || id;
    assert.equal(await page.locator('#chart-selected [data-chart-remove]').count(), new Set(settings.default_indicators.map(canonical)).size);
    for (const rule of Object.values(settings.indicator_families)) {
      assert.equal(await page.locator('#chart-indicators input[value="' + rule.base + '"]').count(), 1);
      assert.equal(await page.locator('#chart-indicators input').evaluateAll((inputs, op) => inputs.filter(i => op.includes(i.value)).length,
        Object.keys(catalog).filter(id => catalog[id].operation === catalog[rule.base].operation)), 1);
    }
    assert.equal(await page.locator('#chart-indicator-settings [data-indicator-group="ma_20"][data-indicator-field="n"]').inputValue(), '20');
    assert.ok((await page.locator('#chart-overlays .overlay-chips').textContent()).includes('60'));
    assert.equal(await page.locator('#chart-config details').count(), 0);
    await page.locator('#stock-quotes').screenshot({path: path.join(out, 'compact.png')});
    checks.push('page/config HTTP 200; one entry per parameterized family; old MA20/MA60 selections migrate without losing periods');

    await page.locator('#chart-config-toggle').click();
    await page.locator('#chart-indicator-search').fill('MACD');
    const visible = await page.locator('#chart-indicators label:visible').allTextContents();
    assert.ok(visible.length && visible.every(label => label.includes('MACD')));
    await page.locator('#chart-indicator-search').fill('no-such-indicator');
    assert.equal(await page.locator('#chart-indicators label:visible').count(), 0);
    assert.ok((await page.locator('#chart-selection-note').textContent()).includes('没有匹配'));
    await page.locator('#chart-indicator-search').fill('');
    for (const key of Object.keys(settings.pattern_presets)) {
      const preset = settings.pattern_presets[key];
      await page.locator('#chart-preset').selectOption(key);
      assert.deepEqual((await page.locator('#chart-indicators input:checked').evaluateAll(inputs => inputs.map(i => i.value))).sort(), [...preset.indicators].sort());
      assert.equal(await page.locator('#chart-selected [data-chart-remove]').count(), preset.indicators.length);
      await page.locator('#chart-tab-settings').click();
      const expected = new Set();
      function expand(id) {
        if (expected.has(id)) return;
        expected.add(id);
        const factor = catalog[id] || {};
        (factor.dependencies || (factor.recipe || {}).features || []).forEach(expand);
      }
      preset.indicators.forEach(expand);
      const expectedGroups = Object.values(config.indicator_controls).filter(group => group.factors.some(id => expected.has(id))).length;
      assert.equal(await page.locator('#chart-indicator-settings fieldset').count(), expectedGroups, key + ' parameter groups');
      await page.locator('#chart-tab-select').click();
    }
    checks.push('search and empty results; every configured preset synchronizes checks, chips and parameter groups');

    while (await page.locator('#chart-selected [data-chart-remove]').count()) await page.locator('#chart-selected [data-chart-remove]').first().click();
    const inputs = page.locator('#chart-indicators input');
    for (let i = 0; i < settings.max_indicators; i++) await inputs.nth(i).check();
    await inputs.nth(settings.max_indicators).click();
    assert.equal(await page.locator('#chart-indicators input:checked').count(), settings.max_indicators);
    assert.ok((await page.locator('#chart-selection-note').textContent()).includes('最多选择'));
    await page.locator('#chart-tab-select').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#chart-tab-settings').getAttribute('aria-selected'), 'true');
    await page.locator('#stock-quotes').screenshot({path: path.join(out, 'settings.png')});
    checks.push('selection limit enforced before request; keyboard tab navigation');

    const overlayKey = Object.keys(settings.overlay_presets)[0];
    const overlayItems = settings.overlay_presets[overlayKey].items;
    if (await page.locator('[data-ov-clear]').count()) await page.locator('[data-ov-clear]').click();
    await page.locator('[data-ov-preset="' + overlayKey + '"]').click();
    assert.equal(await page.locator('#chart-overlays [data-ov-remove]').count(), overlayItems.length);
    assert.ok((await page.locator('#chart-config-count').textContent()).includes(overlayItems.length + ' 组追加参数'));
    await page.locator('[data-ov-preset="' + overlayKey + '"]').click();
    assert.equal(await page.locator('#chart-overlays [data-ov-remove]').count(), overlayItems.length);
    await page.locator('[data-ov-remove="0"]').click();
    assert.equal(await page.locator('#chart-overlays [data-ov-remove]').count(), overlayItems.length - 1);
    await page.locator('[data-ov-clear]').click();
    assert.equal(await page.locator('#chart-overlays [data-ov-remove]').count(), 0);
    checks.push('overlay presets, deduplication, removal and clearing synchronize summary');

    await page.locator('#chart-tab-select').click();
    while (await page.locator('#chart-selected [data-chart-remove]').count()) await page.locator('#chart-selected [data-chart-remove]').first().click();
    await page.locator('#chart-indicators input[value="ema_20"]').check();
    assert.equal(await page.locator('#chart-preset').inputValue(), '');
    await page.locator('#chart-tab-settings').click();
    await page.locator('#chart-indicator-settings [data-indicator-field="n"]').fill('12');
    await page.locator('#chart-indicator-settings [data-indicator-apply]').click();
    await page.locator('[data-ov-preset="' + overlayKey + '"]').click();
    const stocksResponse = await page.request.get(base + '/api/studio/stocks?cached=1&limit=1');
    assert.equal(stocksResponse.status(), 200);
    const stock = (await stocksResponse.json()).stocks[0];
    assert.ok(stock, 'At least one cached stock is required for the chart API check.');
    await page.locator('#chart-code').fill(stock.code);
    await page.locator('#chart-start').fill(stock.end.slice(0, 4) + '-01-01');
    await page.locator('#chart-end').fill(stock.end);
    const chartResponse = page.waitForResponse(r => r.url().includes('/api/research/chart?'));
    await page.locator('#chart-load').click();
    const chart = await chartResponse;
    assert.equal(chart.status(), 200);
    const query = new URL(chart.url()).searchParams;
    assert.deepEqual(query.get('indicators').split(',').sort(), ['ema_20', 'ma_20']);
    assert.equal(JSON.parse(query.get('indicator_params')).ema_20.n, 12);
    assert.equal(JSON.parse(query.get('indicator_params')).ma_20.n, overlayItems[0].params.n);
    assert.deepEqual(JSON.parse(query.get('overlays')), overlayItems.slice(1));
    await page.locator('#stock-chart svg').first().waitFor();
    assert.ok((await page.locator('#stock-chart').textContent()).includes('EMA 指数均线 (12)'));
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('#chart-load').disabled);
    assert.equal(await page.locator('#chart-selected [data-chart-remove="ema_20"]').count(), 1);
    assert.equal(await page.locator('#chart-overlays [data-ov-remove]').count(), overlayItems.length - 1);
    assert.equal(await page.locator('#chart-indicator-settings [data-indicator-group="ema_20"][data-indicator-field="n"]').inputValue(), '12');
    checks.push('chart API 200 with selected factors, custom EMA and overlays; preferences survive reload');

    await page.setViewportSize({width: 390, height: 844});
    await page.evaluate(() => localStorage.setItem('quant.sb.collapsed', '1'));
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('#chart-load').disabled && document.body.classList.contains('sb-collapsed'));
    await page.locator('#chart-config-toggle').click();
    for (const tab of ['select', 'settings']) {
      await page.locator('#chart-tab-' + tab).click();
      const sizes = await page.evaluate(() => ({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth}));
      assert.ok(sizes.scroll <= sizes.width + 2, tab + ' mobile overflow: ' + JSON.stringify(sizes));
    }
    await page.locator('#stock-quotes').screenshot({path: path.join(out, 'mobile.png')});
    checks.push('390px selection/settings layout without horizontal overflow');

    await page.goto(base + '/market');
    await page.locator('#market-chart-config #chart-config-toggle').waitFor();
    assert.equal(await page.locator('#market-chart-config details').count(),0);
    assert.equal(await page.locator('#market-chart-config #chart-config-panel').isVisible(),false);
    checks.push('market page reuses the same consolidated parameterized chart component');
    assert.deepEqual(errors, [], 'Browser JavaScript errors');
    fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify({ok: true, checks, errors}, null, 2));
    console.log(JSON.stringify({ok: true, checks, errors}, null, 2));
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
