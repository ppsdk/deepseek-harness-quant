const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL,out=path.resolve('output/strategy_research/multiroute_v1');
 const entries=JSON.parse(fs.readFileSync(path.join(out,'platform_archives.json'),'utf8'));
 const errors=[],checked=[];
 for(const e of entries){
  const response=await fetch(base+'/api/studio/experiments?id='+encodeURIComponent(e.name));assert.equal(response.status,200);
  const value=await response.json(),source=JSON.parse(fs.readFileSync(path.resolve(e.source_ledger),'utf8'));
  assert.equal(value.metrics.annual_return,source.metrics.annual_return);
  assert.equal(value.metrics.max_drawdown,source.metrics.max_drawdown);
  assert.deepEqual(value.nav,source.nav);assert.equal(value.report.account_available,true);
  assert.ok(value.daily_account.every(a=>a.holding_count<=10));checked.push(e.name);
 }
 const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1100}});page.on('pageerror',e=>errors.push(e.message));
  for(const round of ['round_1','round_2']){
   const e=entries.find(e=>e.round_id===round&&e.route==='matched_ml');
   assert.equal((await page.goto(base+e.url)).status(),200);
   await page.locator('#run-detail.bt-report').waitFor({timeout:45000});
   await page.locator('#run-detail [data-plot] .bt-plot-surface svg').waitFor({timeout:45000});
   assert.ok(await page.locator('#run-detail [data-plot] .bt-plot-surface svg path').count()>=4);
   assert.ok((await page.locator('#run-detail').textContent()).includes('多路线'));
   await page.locator('#run-detail [data-plot]').scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(out,round+'-ml-archive.png')});
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,archive_apis:checked.length,chrome_rendered_reports:2,errors},null,2));
  console.log('Multiroute archive APIs and Chrome reports passed: '+checked.length);
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
