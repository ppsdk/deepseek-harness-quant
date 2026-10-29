/* Real page/API/graph verification for frozen strategy bundles. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL,out=path.resolve('output/strategy_research/strategy_assembly_index');
 fs.mkdirSync(out,{recursive:true});
 const response=await fetch(base+'/api/studio/strategy-assembly');assert.equal(response.status,200);const data=await response.json();
 assert.equal(data.state,'complete');assert.ok(data.entries.length>0);
 const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const errors=[];const archives=[];
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1100}});page.on('pageerror',e=>errors.push(e.message));
  assert.equal((await page.goto(base+'/strategies')).status(),200);
  await page.waitForFunction(()=>document.querySelectorAll('#assembly-results tbody tr').length>0,{},{timeout:45000});
  assert.equal(await page.locator('#assembly-results tbody tr').count(),data.entries.length);
  assert.ok((await page.locator('#assembly-status').textContent()).includes('历史已被观察'));
  await page.locator('#assembly-results').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'strategies-browser.png')});
  for(const study of data.studies){
   const entry=data.entries.find(r=>r.study_id===study.id&&r.scope==='mainboard');
   const check=await fetch(base+'/api/studio/experiments?id='+encodeURIComponent(entry.name));assert.equal(check.status,200);
   const run=await check.json();assert.equal(run.archived,true);assert.equal(run.research_audit.validation_is_fresh_blind,false);
   assert.ok(Math.abs(run.metrics.annual_return-entry.annual_return)<1e-10);
   assert.ok(Math.abs(run.metrics.max_drawdown-entry.max_drawdown)<1e-10);
   assert.equal((await page.goto(base+'/backtests?run='+encodeURIComponent(entry.name))).status(),200);
   await page.locator('#run-detail.bt-report').waitFor({timeout:45000});
   assert.ok((await page.locator('#run-detail').textContent()).includes('前复权'));
   await page.screenshot({path:path.join(out,study.id+'-archive.png')});archives.push(entry.name);
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,page_status:200,api_status:200,rows:data.entries.length,archives,errors},null,2));
  console.log('Assembly strategy page, API and archived graphs passed; configurations='+data.entries.length);
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
