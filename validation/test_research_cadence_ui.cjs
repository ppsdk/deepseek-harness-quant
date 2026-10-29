const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL,out=path.resolve('output/strategy_research/mainboard_low_turnover/event_trading');fs.mkdirSync(out,{recursive:true});
 const html=fs.readFileSync('ui_v2/pages/backtest.html','utf8');for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new vm.Script(script[1]);
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});const errors=[];let submitted;
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/studio/jobs',async route=>{submitted=route.request().postDataJSON();await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'TEST captured'})});});
  assert.equal((await page.goto(base+'/backtest')).status(),200);await page.waitForFunction(()=>!document.querySelector('#bt-run').disabled);
  assert.equal(await page.locator('#bt-rebalance_days').isVisible(),true);assert.equal(await page.locator('#bt-entry_ma_fast').isVisible(),false);
  await page.locator('#bt-trade_mode').selectOption('event');assert.equal(await page.locator('#bt-rebalance_days').isVisible(),false);assert.equal(await page.locator('#bt-entry_ma_fast').isVisible(),true);
  await page.locator('#bt-entry_ma_fast').fill('13');await page.locator('#bt-entry_ma_slow').fill('55');await page.locator('#bt-topn').fill('5');await page.locator('#bt-min_trade_interval_days').fill('7');
  await page.locator('#bt-timing_rule').selectOption('index_strong_risk_ma');await page.locator('#bt-timing_execution').selectOption('daily_exit');
  await page.screenshot({path:path.join(out,'event-controls.png')});await page.locator('#bt-run').click();await page.waitForFunction(()=>document.querySelector('#bt-result').textContent.includes('TEST captured'));
  assert.equal(submitted.action,'backtest');assert.equal(submitted.params.trade_mode,'event');assert.equal(Number(submitted.params.entry_ma_fast),13);assert.equal(Number(submitted.params.entry_ma_slow),55);assert.equal(Number(submitted.params.min_trade_interval_days),7);
  await page.locator('#bt-trade_mode').selectOption('fixed');assert.equal(await page.locator('#bt-rebalance_days').isVisible(),true);
  const config=await(await page.request.get(base+'/api/research/config')).json();assert.ok(config.parameters.trade_mode.options.event);
  const result=await page.request.get(base+'/api/studio/timing?index=000300.SH&rule=index_strong_risk_ma&start=2025-01-01&end=2025-12-31');assert.equal(result.status(),200);
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,errors,event_mode_submission:true,custom_ma_periods:true,legacy_mode_preserved:true,timing_api_status:200},null,2));console.log('Event trading browser and timing API: OK');
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
