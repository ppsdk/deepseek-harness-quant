const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL,out=path.resolve('output/strategy_research/technical_rule_index/verification');fs.mkdirSync(out,{recursive:true});
 const response=await fetch(base+'/api/studio/technical-rules');assert.equal(response.status,200);const index=await response.json();
 assert.equal(index.state,'complete');assert.ok(index.entries.length>0);assert.equal(index.studies.length,3);
 const study=JSON.parse(fs.readFileSync('output/strategy_research/technical_rule_index/研究结论.json','utf8'));
 assert.equal(index.entries.length,study.combinations);assert.equal(index.ledger_count,study.ledgers);
 const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const errors=[],checked=[];
 try{
  const page=await browser.newPage({viewport:{width:1500,height:1100}});page.on('pageerror',e=>errors.push(e.message));
  for(const entry of study.representatives){
   const r=await (await fetch(base+'/api/studio/experiments?id='+entry.run)).json();assert.equal(r.strategy_spec.kind,'classical_rules');assert.deepEqual(r.factors,[]);assert.ok(r.daily_account.length>1000);
   assert.equal((await page.goto(base+entry.url)).status(),200);
   const shelf=page.locator('[data-rule-studies]');await shelf.waitFor();await shelf.locator('summary').click();assert.equal(await shelf.locator('tbody tr').count(),index.entries.length);
   const replay=page.locator('#run-detail [data-rule-replay]');await replay.waitFor();
   const first=r.trades[0].date;await replay.locator('[data-rule-event]').selectOption(first);
   assert.equal(await replay.getAttribute('data-selected-date'),first);
   const actual=r.daily_account.find(a=>a.date===first),fills=r.trades.filter(t=>t.date===first);
   assert.equal(await replay.locator('[data-rule-fills] tbody tr').count(),fills.length);
   assert.equal(await replay.locator('[data-rule-positions] tbody tr').count(),actual.holding_count);
   await page.locator('[data-rule-description] summary').click();assert.match(await page.locator('[data-rule-description]').textContent(),/无因子|没有因子/);
   const marker=page.locator('#run-detail [data-plot] .bt-trade-event').first(),date=await marker.getAttribute('data-date');
   await marker.click({force:true});assert.equal(await replay.getAttribute('data-selected-date'),date);
   await replay.locator('[data-rule-date]').fill(r.dates.at(-1));await replay.locator('[data-rule-date]').dispatchEvent('change');
   assert.equal(await replay.getAttribute('data-selected-date'),r.dates.at(-1));
   await replay.locator('[data-rule-event]').selectOption(first);
   await replay.screenshot({path:path.join(out,entry.strategy+'-'+entry.variant+'.png')});
   await replay.locator('[data-rule-code]').first().click();
   await page.waitForFunction(day=>document.querySelector('#run-detail [data-fill-chart] .research-chart-surface')?.dataset.focusDate===day,first,{timeout:45000});
   assert.ok(await page.locator('#run-detail [data-fill-chart] .research-trade-marker').count()>0);
   checked.push({run:entry.run,days:r.dates.length,first_trade:first});
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,index_entries:index.entries.length,checked,errors},null,2));
  console.log('Classical-rule catalog, descriptions, date replay, fills and K-line links passed: '+checked.length);
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
