/* Live keyless quotes and the exact same parameterized chart controls as the workbench. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL,out=path.resolve('output/market_cloud');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});const errors=[],checks=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});page.on('pageerror',e=>errors.push(e.message));
  assert.equal((await page.goto(base+'/market')).status(),200);
  await page.locator('#market-chart-config #chart-config-toggle:not([disabled])').waitFor();
  await page.locator('#market-analysis table tbody tr').first().waitFor({timeout:60000});
  assert.ok((await page.locator('#market-analysis').textContent()).includes('腾讯财经'));
  assert.equal((await page.locator('#market-analysis').textContent()).includes('本机已下载个股'),false);
  assert.equal(await page.locator('[data-ma-refresh]').textContent(),'刷新云端分析');
  assert.equal(await page.locator('#market-chart-config #chart-config-panel').isVisible(),false);
  await page.locator('#market-chart-config #chart-config-toggle').click();await page.locator('#chart-tab-settings').click();
  // Reusing the shared component migrates old MA20/60 preferences into one parameterized type.
  assert.equal(await page.locator('#chart-indicators input[value="ma_20"]').count(),1);
  assert.equal(await page.locator('#chart-indicators input[value="ma_60"]').count(),0);
  while(await page.locator('[data-chart-remove]').count())await page.locator('[data-chart-remove]').first().click();
  await page.locator('#chart-tab-select').click();await page.locator('#chart-indicators input[value="ema_20"]').check();
  await page.locator('#chart-tab-settings').click();await page.locator('#chart-indicator-settings [data-indicator-field="n"]').fill('13');
  const response=page.waitForResponse(r=>r.url().includes('/api/studio/market?')&&new URL(r.url()).searchParams.get('indicator_params')?.includes('13'));
  await page.locator('[data-indicator-apply]').click();const data=await(await response).json();
  assert.equal(data.data_source,'cloud');assert.equal(data.price_source,'新浪财经在线K线');
  assert.equal(data.local_calculation,true);assert.equal(data.local_price_access,false);
  assert.ok(data.price_coverage.rows>100);assert.ok(data.bars.length);assert.ok(data.indicators.ema_20);
  await page.locator('[data-ov-base]').selectOption('ema_20');await page.locator('[data-ov-batch]').fill('7,13');await page.locator('[data-ov-add]').click();
  assert.ok((await page.locator('#chart-selected').textContent()).includes('7'));
  await page.locator('#market-chart svg').first().waitFor();await page.screenshot({path:path.join(out,'market.png'),fullPage:false});
  checks.push('live quotes and chart HTTP 200; source dates shown; banner does not perform analytics from the local stock database; shared EMA parameter and overlays');

  const combo=await page.request.get(base+'/api/studio/market?'+new URLSearchParams({
    index:'000300.SH',timeframe:'day',start:'2026-01-01',end:'2026-10-27',
    indicators:'ema_20,macd_dif,macd_hist,rsi_divergence',
    indicator_params:JSON.stringify({'ema_20':{n:13},'technical:macd':{fast:10,slow:20,signal:5}}),
    overlays:JSON.stringify([{base:'ema_20',params:{n:21}}])
  }));
  assert.equal(combo.status(),200);const combined=await combo.json();
  assert.equal(combined.local_calculation,true);assert.equal(combined.local_price_access,false);
  assert.equal(combined.indicator_params.ema_20.n,13);assert.equal(combined.indicator_params['technical:macd'].fast,10);
  assert.ok(combined.indicators.ema_20);assert.ok(combined.indicators['ema_20#1']);assert.ok(combined.indicators.macd_dif);assert.ok(combined.indicators.macd_hist);
  assert.ok(combined.patterns.studies.some(item=>item.id==='rsi_divergence'));
  checks.push('live cloud Kline drives arbitrary EMA/MACD params and RSI-divergence shape engine');

  const periods=await(await page.request.get(base+'/api/research/config')).json();
  for(const timeframe of Object.keys(periods.chart_settings.timeframes)){
   const r=await page.request.get(base+'/api/studio/market?'+new URLSearchParams({index:'000300.SH',timeframe,indicators:'ema_20',indicator_params:JSON.stringify({ema_20:{n:13}}),start:'2026-01-01',end:'2026-10-27'}));
   assert.equal(r.status(),200);const body=await r.json();assert.ok(body.bars.length,timeframe+' no bars');
   assert.equal(body.local_calculation,true);assert.equal(body.local_price_access,false);
  }
  checks.push('live daily/weekly/monthly cloud bars with workbench indicators');
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>localStorage.setItem('quant.sb.collapsed','1'));await page.reload();
  await page.locator('#chart-config-toggle:not([disabled])').waitFor();await page.locator('#chart-config-toggle').click();await page.locator('#chart-tab-settings').click();
  const sizes=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));assert.ok(sizes.scroll<=sizes.width+2,JSON.stringify(sizes));
  await page.screenshot({path:path.join(out,'mobile.png')});assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,checks,errors,cloud_date:data.bars.at(-1).date,price_coverage:data.price_coverage},null,2));console.log(JSON.stringify({ok:true,checks,errors}));
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});