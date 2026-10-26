/* Local end-to-end authoring, candidate signals and gap inspection. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL||'http://127.0.0.1:8787',out=path.resolve('output/platform_audit/workflow');fs.mkdirSync(out,{recursive:true});
 const b=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'}),p=await b.newPage({viewport:{width:1440,height:1000}});p.setDefaultTimeout(20000);
 const record={errors:[],checks:[]};p.on('pageerror',e=>record.errors.push(e.message));
 async function post(url,payload){const r=await p.request.post(base+url,{data:payload});assert.equal(r.status(),200,await r.text());return r.json();}
 try{
  await p.goto(base+'/factors');await p.waitForSelector('#formula-template');
  await p.locator('#formula-template').selectOption('ma');await p.locator('#formula-use').click();
  assert.equal(await p.locator('#factor-formula').inputValue(),'MA(close, 20)');
  await p.locator('#factor-formula').fill('REF(close, -1)');await p.locator('#formula-validate').click();await p.waitForFunction(()=>document.querySelector('#factor-save-status').textContent.includes('周期'));
  await p.locator('#factor-formula').fill('MA(close, 3)');await p.locator('#formula-validate').click();await p.waitForFunction(()=>document.querySelector('#factor-save-status').textContent.includes('检查通过'));
  for(const [k,v] of Object.entries({codes:'000001.SZ',start:'2025-01-01',end:'2025-06-30',stocks:'1'}))await p.locator('#formula-preview-params [data-key="'+k+'"]').fill(v);
  await p.locator('#formula-preview').click();await p.waitForSelector('#formula-preview-chart svg',{timeout:60000});
  assert.ok((await p.locator('#formula-preview-status').textContent()).includes('有效值'));await p.locator('#formula-preview').scrollIntoViewIfNeeded();await p.screenshot({path:path.join(out,'formula-preview.png')});
  const details=p.locator('details').filter({has:p.locator('#formula-build')});await details.locator('summary').click();
  await p.locator('[data-left]').fill('RSI(close,14)');await p.locator('[data-right]').fill('30');await p.locator('#formula-build').click();await p.waitForFunction(()=>document.querySelector('#factor-formula').value.includes('RSI(close,14)'));
  record.checks.push('formula templates, invalid future reference, preview and condition builder');
  const pool=await post('/api/studio/pools',{name:'功能验收候选名单',codes:['000001.SZ','000002.SZ'],signal_date:'2020-01-01'});record.pool=pool.id;
  await p.goto(base+'/signals?pool_id='+pool.id);await p.waitForSelector('#signal-preset');await p.locator('#signal-preset').selectOption('ma_cross');await p.locator('#signal-use').click();await p.locator('#signal-name').fill('功能验收均线条件');
  const saved=p.waitForResponse(r=>r.url().endsWith('/api/studio/signals')&&r.request().method()==='POST');await p.locator('#signal-save').click();const plan=await(await saved).json();assert.equal(plan.ok,true);record.plan=plan.id;
  await p.waitForFunction(id=>document.querySelector('#signal-plan').value===id,plan.id);await p.locator('#signal-end').fill('2025-06-30');await p.locator('#signal-check').click();await p.waitForSelector('#signal-results tbody tr',{timeout:60000});assert.equal(await p.locator('#signal-results tbody tr').count(),2);
  await p.locator('[data-signal-code]').first().click();await p.waitForSelector('#signal-chart svg');assert.ok(await p.locator('#signal-chart [data-event="user_entry"]').count()>0);await p.locator('#signal-chart').scrollIntoViewIfNeeded();await p.screenshot({path:path.join(out,'signals.png')});
  const href=await p.locator('#signal-backtest').getAttribute('href');assert.ok(href.includes(plan.id));
  await p.goto(base+href+'&start=2024-01-01&end=2025-06-30&topn=2&rebalance_days=5');await p.waitForFunction(()=>!document.querySelector('#bt-run').disabled);
  assert.equal(await p.locator('#bt-signal_plan_id').inputValue(),plan.id);assert.equal(await p.locator('#bt-pool_id').inputValue(),pool.id);
  const submitted=p.waitForResponse(r=>r.url().endsWith('/api/studio/jobs')&&r.request().method()==='POST');await p.locator('#bt-run').click();const job=await(await submitted).json();await p.waitForSelector('#bt-result.bt-report',{timeout:60000});
  const status=await(await p.request.get(base+'/api/studio/jobs/'+job.id)).json();const run=await(await p.request.get(base+status.result_url)).json();assert.ok(run.signal_plan);assert.ok(run.trades.some(t=>t.trigger==='technical_entry'));assert.ok(run.trades.some(t=>t.trigger==='technical_exit'));assert.ok(run.trades.every(t=>t.signal_date<t.date));record.run=run.name;
  record.checks.push('save list and plan, historical check, chart markers, next-open signal backtest');
  await p.goto(base+'/data');await p.waitForSelector('#quality-check');for(const [k,v] of Object.entries({codes:'000001.SZ,000002.SZ',start:'2019-01-01',end:'2025-06-30'}))await p.locator('#quality-params [data-key="'+k+'"]').fill(v);
  await p.locator('#quality-check').click();await p.waitForFunction(()=>document.querySelector('#quality-summary b')?.textContent==='2',null,{timeout:60000});assert.equal(await p.locator('#quality-summary b').first().textContent(),'2');await p.locator('#quality-summary').scrollIntoViewIfNeeded();await p.screenshot({path:path.join(out,'data-quality.png')});record.checks.push('real cache missing-date and field audit');
  await p.setViewportSize({width:390,height:844});for(const url of ['/factors','/signals','/data']){await p.goto(base+url);await p.waitForSelector('#studio-content section');await p.waitForFunction(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2);}
  record.checks.push('390px layouts');assert.deepEqual(record.errors,[]);
 }finally{fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(record,null,2));await b.close();}
 console.log(JSON.stringify(record,null,2));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
