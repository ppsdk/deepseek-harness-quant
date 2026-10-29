/* Browser contracts for configurable overlays, updates, candidate signals and ML. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const base=process.env.RESEARCH_BASE_URL, out=path.resolve('output/workspace_repairs');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const errors=[],checks=[],posts=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});page.on('pageerror',e=>errors.push(e.message));
  // Inspect submissions without launching downloads, training jobs or mutating real pools.
  await page.route('**/api/studio/jobs',async route=>{posts.push(route.request().postDataJSON());await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'TEST: submission captured'})});});
  for(const route of ['/data','/ml','/signals','/research']){
   const r=await page.goto(base+route);assert.equal(r.status(),200);
   if(route==='/data'){
    await page.locator('[data-update="stocks_retry"]').waitFor();assert.equal(await page.locator('#data-updates button').count(),4);
    await page.waitForFunction(()=>document.querySelector('#data-auto-status').textContent.includes('TEST:'));
    const automatic=posts.find(p=>p.action==='stocks');assert.ok(automatic);assert.equal(posts.filter(p=>p.action==='stocks').length,1);
    assert.equal(automatic.params.start,'2019-01-01');assert.equal(automatic.params.end,'');assert.equal(automatic.params.codes,'');assert.equal(automatic.params.retry_failed,false);
    await page.waitForFunction(()=>document.querySelector('#finance-quarter-select').options.length>0);
    assert.equal(await page.locator('#quality-check').count(),1);
    await page.locator('#data-download-params [data-key="start"]').fill('2024-01-01');
    await page.locator('#data-download-params [data-key="codes"]').fill('000001.SZ');
    await page.locator('[data-update="stocks_retry"]').click();await page.waitForFunction(()=>document.querySelector('#data-update-status').textContent.includes('TEST:'));
    assert.equal(posts.at(-1).action,'stocks');assert.equal(posts.at(-1).params.retry_failed,true);assert.equal(posts.at(-1).params.start,'2024-01-01');
    await page.screenshot({path:path.join(out,'data.png')});checks.push('data: four API-driven update actions, date/code inputs and failed-only retry payload');
   }
   if(route==='/ml'){
    await page.locator('#ml-recipe [data-key="horizon"]').waitFor();assert.ok(await page.locator('#ml-model option').count()>2);
    assert.equal(await page.locator('#ml-features input:checked').count(),5);
    assert.equal(await page.locator('#ml-features').evaluate(e=>getComputedStyle(e).display),'grid');
    assert.ok(await page.locator('#ml-features').evaluate(e=>e.clientHeight<=330));
    await page.locator('#ml-model').selectOption('ridge');await page.locator('#ml-recipe [data-key="horizon"]').fill('7');
    await page.locator('[data-prep="normalization"]').selectOption('zscore');
    await page.locator('[data-prep="scaling"]').selectOption('standard');
    await page.locator('[data-prep="correlation_filter"]').selectOption('greedy');
    await page.locator('[data-prep="max_abs_correlation"]').fill('0.5');
    await page.locator('[data-prep="min_features"]').fill('3');
    await page.locator('[data-prep="reduction"]').selectOption('pca');
    await page.locator('[data-prep="component_mode"]').selectOption('count');
    await page.locator('[data-prep="n_components"]').fill('2');
    assert.equal(await page.locator('[data-prep="variance_ratio"]').isVisible(),false);
    await page.locator('#ml-run').click();await page.waitForFunction(()=>document.querySelector('#ml-status').textContent.includes('TEST:'));
    const p=posts.at(-1);assert.equal(p.action,'ml');assert.equal(p.params.recipe.model,'ridge');assert.equal(p.params.recipe.horizon,7);assert.equal(p.params.recipe.features.length,5);assert.ok(!('split_date' in p.params));
    assert.equal(p.params.recipe.preprocessing.normalization,'zscore');assert.equal(p.params.recipe.preprocessing.scaling,'standard');assert.equal(p.params.recipe.preprocessing.reduction,'pca');assert.equal(p.params.recipe.preprocessing.n_components,2);
    assert.equal(p.params.recipe.preprocessing.correlation_filter,'greedy');assert.equal(p.params.recipe.preprocessing.max_abs_correlation,0.5);assert.equal(p.params.recipe.preprocessing.min_features,3);
    await page.locator('[data-prep="n_components"]').fill('6');await page.locator('#ml-run').click();assert.ok((await page.locator('#ml-status').textContent()).includes('PCA'));
    await page.screenshot({path:path.join(out,'ml.png')});checks.push('ML: populated models/default features, bounded layout, arbitrary horizon and nested recipe submission');
   }
   if(route==='/signals'){
    await page.locator('#signal-template-params [data-key="fast"]').waitFor();
    await page.locator('#signal-template-params [data-key="fast"]').fill('7');await page.locator('#signal-template-params [data-key="slow"]').fill('31');
    await page.locator('#signal-template-params [data-key="slow"]').dispatchEvent('change');
    assert.ok((await page.locator('#signal-entry').inputValue()).includes('7'));assert.ok((await page.locator('#signal-entry').inputValue()).includes('31'));
    await page.locator('#signal-new-pool-name').fill('Browser research');await page.locator('#signal-screen').click();
    await page.waitForFunction(()=>document.querySelector('#signal-pool-status').textContent.includes('TEST:'));
    assert.equal(posts.at(-1).action,'candidate_screen');assert.equal(posts.at(-1).params.pool_name,'Browser research');
    assert.ok(await page.locator('#signal-state option').count()>2);await page.screenshot({path:path.join(out,'signals.png')});
    checks.push('signals: numeric templates, shared strategy candidate job, manual pool controls and state filters');
   }
   if(route==='/research'){
    await page.waitForFunction(()=>!document.querySelector('#chart-load').disabled);await page.locator('#chart-config-toggle').click();await page.locator('#chart-tab-settings').click();
    if(await page.locator('[data-ov-clear]').count())await page.locator('[data-ov-clear]').click();
    await page.locator('[data-ov-base]').selectOption('ma_20');await page.locator('[data-ov-batch]').fill('7,13,34,89');await page.locator('[data-ov-add]').click();
    assert.equal(await page.locator('[data-ov-edit]').count(),4);await page.locator('[data-ov-edit="1"]').click();await page.locator('[data-ov-field="n"]').fill('17');await page.locator('[data-ov-add]').click();
    assert.ok((await page.locator('.overlay-chips').textContent()).includes('17'));
    assert.ok(await page.locator('[data-ov-base] option').evaluateAll(opts=>opts.some(o=>o.value.includes('naked')||o.value.includes('gaussian'))));
    await page.locator('[data-ov-base]').selectOption('ma_20');await page.locator('[data-ov-batch]').fill('7,13,34,89');await page.locator('[data-ov-add]').click();
    assert.equal(await page.locator('[data-ov-edit]').count(),5); // re-add replaced 13; existing periods deduplicate
    await page.screenshot({path:path.join(out,'parameters.png')});checks.push('overlays: arbitrary period batch, edit, semantic deduplication, pattern parameter selection');
   }
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'browser_verification.json'),JSON.stringify({ok:true,checks,errors},null,2));console.log(JSON.stringify({ok:true,checks,errors},null,2));
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
