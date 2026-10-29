const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL,out=path.resolve('output/strategy_research/trade_replay_verification');fs.mkdirSync(out,{recursive:true});
 const index=await (await fetch(base+'/api/studio/trade-replay')).json();assert.ok(index.entries.length>=4);
 const errors=[],checked=[];
 const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1100}});page.on('pageerror',e=>errors.push(e.message));
  for(const entry of index.entries){
   const response=await fetch(base+'/api/studio/trade-replay?id='+entry.run);assert.equal(response.status,200);
   const summary=await response.json();assert.equal(summary.available,true);assert.equal(summary.verification.daily_market_value_reconciled,true);
   if(entry.round_id==='round_annual_expanding')assert.equal(summary.fit_schedule.length,6);
   const sourceResponse=await fetch(base+'/api/studio/experiments?id='+entry.run);assert.equal(sourceResponse.status,200);const source=await sourceResponse.json();
   for(const day of [summary.dates[0],summary.event_dates[0],summary.event_dates.at(-1),summary.dates.at(-1)]){
    const data=await (await fetch(base+'/api/studio/trade-replay?'+new URLSearchParams({id:entry.run,date:day}))).json();
    const d=data.detail,a=source.daily_account.find(a=>a.date===day);
    assert.equal(d.cash,a.cash);assert.equal(d.equity,a.equity);assert.equal(d.positions.length,a.holding_count);
    assert.ok(Math.abs(d.positions.reduce((s,p)=>s+p.market_value,0)-a.market_value)<.02);
    for(const t of d.trades){assert.ok(t.fit_evidence.last_outcome_date<=t.signal_date);assert.ok(t.fit_evidence.fit_end<t.date);}
   }
   assert.equal((await page.goto(base+entry.url)).status(),200);
   const replay=page.locator('#run-detail .bt-replay');await replay.locator('[data-replay-event]').waitFor({timeout:45000});
   const first=summary.event_dates[0];await replay.locator('[data-replay-event]').selectOption(first);
   await page.waitForFunction(day=>document.querySelector('#run-detail .bt-replay')?.dataset.selectedDate===day,first);
   assert.ok(await replay.locator('[data-replay-fills] tbody tr').count()>0);
   assert.ok(await replay.locator('[data-replay-positions] tbody tr').count()<=10);
   await replay.locator('[data-strategy-description] summary').click();assert.ok((await replay.textContent()).includes('最后标签终点'));
   assert.equal(await replay.locator('[data-strategy-description] table').last().locator('tbody tr').count(),summary.fit_schedule.length);
   await replay.locator('[data-strategy-description] summary').click();
   await replay.locator('[data-replay-ranking] summary').click();assert.ok((await replay.locator('[data-replay-candidates]').textContent()).includes('该日使用的模型'));
   await replay.locator('[data-replay-ranking] summary').click();
   // A click on the portfolio curve must select its date in the replay.
   const marker=page.locator('#run-detail [data-plot] .bt-trade-event').first(),markerDate=await marker.getAttribute('data-date');
   await marker.click({force:true});await page.waitForFunction(day=>document.querySelector('#run-detail .bt-replay')?.dataset.selectedDate===day,markerDate);
   await replay.locator('[data-replay-event]').selectOption(first);
   await page.waitForFunction(day=>document.querySelector('#run-detail .bt-replay')?.dataset.selectedDate===day,first);
   await replay.screenshot({path:path.join(out,entry.round_id+'-replay.png')});
   await replay.locator('[data-replay-code]').first().click();
   await page.waitForFunction(day=>document.querySelector('#run-detail [data-fill-chart] .research-chart-surface')?.dataset.focusDate===day,first,{timeout:45000});
   assert.ok(await page.locator('#run-detail [data-fill-chart] .research-trade-marker').count()>0);
   checked.push({run:entry.run,days:summary.dates.length,events:summary.event_dates.length});
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,entries:checked,errors},null,2));
  console.log('Trade replay API, curve/date/stock chart links and Chrome checks passed: '+checked.length);
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
