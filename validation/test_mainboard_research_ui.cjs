/* Read-only regression: named index benchmark, metrics, plots and CSV headers. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL||'http://127.0.0.1:8787';
 const out=path.resolve('output/strategy_research/mainboard_low_turnover');
 const entries=JSON.parse(fs.readFileSync(path.join(out,'final_archives.json'),'utf8'));
 const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const errors=[],checks=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true});
  page.on('pageerror',error=>errors.push(error.message));
  for(const entry of [entries[2],entries[entries.length-3]]){
   const response=await page.goto(base+'/backtests?run='+encodeURIComponent(entry.name));
   assert.equal(response.status(),200);
   await page.locator('#run-detail.bt-report').waitFor();
   const host=page.locator('#run-detail');
   assert.ok((await host.locator('.bt-report-kpis').textContent()).includes('年化超额（百分点）'));
   const legend=await host.locator('[data-plot] .bt-plot-legend').textContent();
   assert.ok(legend.includes('沪深300价格指数'));
   assert.equal(legend.includes('等权'),false);
   await host.locator('[data-mode]').selectOption('relative');
   assert.ok((await host.locator('[data-plot] .bt-plot-legend').textContent()).includes('策略净值 ÷ 沪深300价格指数净值'));
   await host.locator('[data-mode]').selectOption('nav');
   const downloadPromise=page.waitForEvent('download');
   await host.locator('[data-export="nav"]').click();
   const download=await downloadPromise;
   const target=path.join(out,entry.round_id+'_nav.csv');await download.saveAs(target);
   assert.ok(fs.readFileSync(target,'utf8').split('\n')[0].includes('沪深300价格指数'));
   checks.push(entry.round_id+': index plot labels, excess KPIs, relative mode and CSV header');
  }
  await page.screenshot({path:path.join(out,'benchmark-report.png'),fullPage:false});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,checks,errors},null,2));
  console.log(JSON.stringify({ok:true,checks,errors},null,2));
 }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
