/* Read-only real API/render verification for the mined factor families. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL,out=path.resolve('output/strategy_research/four_axis_v1');
 const response=await fetch(base+'/api/studio/factor-mining');assert.equal(response.status,200);const data=await response.json();
 const configResponse=await fetch(base+'/api/research/config');assert.equal(configResponse.status,200);const config=await configResponse.json();
 const parameterResponse=await fetch(base+'/api/studio/factor-parameters');assert.equal(parameterResponse.status,200);const parameters=await parameterResponse.json();
 assert.ok(config.factor_catalog.axis_valuation_ep.available);assert.ok(config.factor_catalog.axis_momentum_risk_adjusted.params.n>0);
 const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
  assert.equal((await page.goto(base+'/factors')).status(),200);
  await page.waitForFunction(()=>document.querySelectorAll('#axis-results tbody tr').length>0,{},{timeout:45000});
  assert.equal(await page.locator('#axis-results tbody tr').count(),data.records.length);
  assert.ok((await page.locator('#axis-status').textContent()).includes(data.data_end));
  for(const [key,label] of Object.entries(data.categories))assert.ok((await page.locator('#axis-counts').textContent()).includes(label+data.effective_counts[key]+' / '+data.minimum_per_category));
  if(data.correlation.pca)assert.ok((await page.locator('#axis-preprocessing').textContent()).includes('PCA'));
  await page.waitForFunction(()=>document.querySelector('#parameter-research-status').textContent.length>0,{},{timeout:45000});
  assert.equal(await page.locator('#parameter-research-results tbody tr').count(),parameters.records.length);
  assert.equal(await page.locator('#parameter-budget-results tbody tr').count(),parameters.budget_records.length);
  if(parameters.state==='complete')assert.ok((await page.locator('#parameter-research-status').textContent()).includes('已完成'));
  await page.locator('#axis-refresh').click();
  await page.locator('#axis-counts').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'four-axis-browser.png')});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,api_status:response.status,page_status:200,rows:data.records.length,counts:data.effective_counts,errors},null,2));
  await page.locator('#parameter-research-status').scrollIntoViewIfNeeded();await page.screenshot({path:path.resolve('output/strategy_research/factor_parameter_v1/parameter-browser.png')});
  fs.writeFileSync(path.resolve('output/strategy_research/factor_parameter_v1/browser_verification.json'),JSON.stringify({ok:true,state:parameters.state,rows:parameters.records.length,errors},null,2));
  await page.locator('#parameter-budget-status').scrollIntoViewIfNeeded();await page.screenshot({path:path.resolve('output/strategy_research/factor_parameter_budget_v1/budget-browser.png')});
  fs.writeFileSync(path.resolve('output/strategy_research/factor_parameter_budget_v1/browser_verification.json'),JSON.stringify({ok:true,rows:parameters.budget_records.length,errors},null,2));
  console.log('Four-axis API and browser passed; rendered rows='+data.records.length);
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
