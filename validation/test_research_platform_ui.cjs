/* Local Chrome end-to-end regression; only local research experiments are written. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL||'http://127.0.0.1:8787',out=path.resolve('output/platform_audit');
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const findings={pages:[],errors:[],checks:[]};
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(20000);
  page.on('pageerror',e=>findings.errors.push({url:page.url(),error:e.message}));
  async function get(url){const r=await page.request.get(base+url);assert.equal(r.status(),200,url);return r.json();}
  const cfg=await get('/api/research/config');assert.ok(cfg.page_sizes);assert.ok(cfg.indicator_controls['mom_20']);
  const stocks=(await get('/api/studio/stocks?cached=1&limit=6')).stocks,codes=stocks.map(s=>s.code).join(',');
  for(const url of ['/research','/strategies','/factors','/ml','/timing','/market','/data','/positions','/backtests']){
   const r=await page.goto(base+url);assert.equal(r.status(),200);
   if(url==='/research')await page.waitForFunction(()=>!document.querySelector('#screen-run').disabled);
   else await page.waitForFunction(()=>document.querySelector('#studio-content')?.children.length>0);
   findings.pages.push(url);
   if(url==='/strategies'){
    await page.locator('#strategy-list article').first().waitFor();assert.ok(await page.locator('#strategy-list article').count()<=cfg.page_sizes.strategies);
    const first=await page.locator('#strategy-list article').first().getAttribute('data-catalog');
    await page.locator('#strategy-pages [data-page-next]').click();assert.notEqual(await page.locator('#strategy-list article').first().getAttribute('data-catalog'),first);
    await page.screenshot({path:path.join(out,'strategies.png'),fullPage:false});
   }
   if(url==='/factors'){
    assert.ok(await page.locator('#factor-list tbody tr').count()<=cfg.page_sizes.factors);
    await page.locator('#analysis-factor').selectOption('ema_20');await page.locator('#analysis-indicators summary').click();
    await page.locator('#analysis-indicators [data-indicator-field="n"]').fill('3');
    for(const [key,value] of Object.entries({start:'2025-01-01',end:'2025-04-30',stocks:'6',topn:'2',codes}))await page.locator('#analysis-params [data-key="'+key+'"]').fill(value);
    await page.locator('#analysis-run').click();await page.waitForFunction(()=>document.querySelector('#analysis-result').textContent.includes('平均排序相关性'),null,{timeout:60000});
    findings.checks.push('factor analysis with custom EMA window');
   }
   if(url==='/market'){
    await page.locator('#market-indicators input').evaluateAll(els=>els.forEach(i=>{i.checked=i.value==='ema_20';}));
    await page.locator('#market-load').click();await page.locator('#market-indicator-settings summary').click();
    await page.locator('#market-indicator-settings [data-indicator-field="n"]').fill('5');
    await page.locator('#market-indicator-settings [data-indicator-apply]').click();
    await page.waitForFunction(()=>document.querySelector('#market-chart').textContent.includes('本次修改的指标参数'));
   }
  }
  const params={strategy:'reversal20',start:'2024-01-01',end:'2025-06-30',stocks:'6',topn:'2',codes,
    rebalance_days:'5',initial_capital:'100000',target_exposure_pct:'60',max_position_pct:'20',stop_loss_pct:'8'};
  await page.goto(base+'/backtest?'+new URLSearchParams(params));await page.waitForFunction(()=>!document.querySelector('#bt-run').disabled);
  await page.locator('#bt-indicator-settings summary').click();await page.locator('#bt-indicator-settings [data-indicator-field="n"]').fill('5');
  const submitted=page.waitForResponse(r=>r.url().endsWith('/api/studio/jobs')&&r.request().method()==='POST');
  await page.locator('#bt-run').click();const job=await(await submitted).json();assert.ok(job.id);
  await page.reload();await page.waitForSelector('#bt-result.bt-report',{timeout:60000});
  const state=await get('/api/studio/jobs/'+job.id);assert.equal(state.state,'done');const run=await get(state.result_url);
  assert.equal(run.params.indicator_params.mom_20.n,5);assert.equal(run.params.stop_loss_pct,8);assert.equal(run.params.target_exposure_pct,60);
  assert.ok(run.provenance.source_hash);assert.ok(run.trades.length);assert.ok(run.position_details);
  findings.run=run.name;findings.checks.push('custom parameters, async backtest and reload recovery');
  await page.locator('#bt-result details').filter({has:page.locator('summary',{hasText:'本次参数、仓位规则与期末持仓'})}).locator('summary').click();
  await page.screenshot({path:path.join(out,'backtest-parameters.png'),fullPage:false});
  const fill=run.trades[0];await page.locator('#bt-result [data-show-date="'+fill.date+'"]').first().click();
  await page.waitForFunction(()=>document.querySelector('[data-fill-chart] .research-chart-surface svg'));
  const range=await page.locator('[data-fill-chart] .research-chart-info').allTextContents();
  assert.ok(range.some(s=>s.includes(fill.code)));assert.equal(await page.locator('[data-fill-chart] .research-chart-surface').getAttribute('data-focus-date'),fill.date);
  await page.goto(base+'/positions?run='+run.name);await page.waitForFunction(()=>document.querySelector('#position-status')?.textContent.includes('截至'));
  await page.locator('#plan-codes').fill(codes);
  for(const [key,value] of Object.entries({end:'2025-06-30',initial_capital:'100000',target_exposure_pct:'60',max_position_pct:'10'}))await page.locator('#plan-params [data-key="'+key+'"]').fill(value);
  await page.locator('#plan-run').click();await page.waitForSelector('#plan-result tbody tr');
  assert.equal(await page.locator('#plan-result tbody tr').count(),6);assert.ok((await page.locator('#plan-result').textContent()).includes('40,000'));
  await page.screenshot({path:path.join(out,'positions.png'),fullPage:true});findings.checks.push('archived positions and budget');
  await page.goto(base+'/backtests?run='+run.name);await page.waitForSelector('#run-detail.bt-report');
  assert.ok(await page.locator('#runs-list tbody tr').count()<=cfg.page_sizes.backtests);
  const old='research_reversal20_bb3b28a808104815ba55364d7632e1a1';
  await page.goto(base+'/backtests?run='+old);await page.waitForSelector('#run-detail.bt-report');
  assert.ok((await page.locator('#run-detail').textContent()).includes('旧实验没有逐股成本'));findings.checks.push('old archive compatibility');
  await page.setViewportSize({width:390,height:844});
  for(const url of ['/research','/positions','/backtest','/factors']){
   await page.goto(base+url);await page.waitForFunction(()=>document.querySelector('.research-workflow a')||document.querySelector('#studio-content')?.children.length||document.querySelector('#bt-topn'));
   await page.waitForFunction(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2,null,{timeout:5000});
   const sizes=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
   assert.ok(sizes.scroll<=sizes.width+2,url+' overflows '+JSON.stringify(sizes));
   const layout=await page.locator('.v2-wrap').boundingBox();assert.ok(layout.x<25&&layout.width>=360,url+' squeezed by sidebar');
  }
  await page.screenshot({path:path.join(out,'mobile-factors.png'),fullPage:false});findings.checks.push('390px layout');
  assert.deepEqual(findings.errors,[]);
 }finally{fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(findings,null,2));await browser.close();}
 console.log(JSON.stringify(findings,null,2));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
