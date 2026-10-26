/* Shared HTTP behavior without a server or third-party libraries. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function client(fetch) {
  const context = {window: {}, fetch, AbortController, setTimeout, clearTimeout, document: {hidden:false}};
  vm.runInNewContext(fs.readFileSync('ui_v2/core/api.js','utf8'),context);
  return context.window.LW.api;
}
function response(status,data) { return {status,ok:status>=200&&status<300,text:async()=>JSON.stringify(data)}; }
async function main() {
  let calls=0;
  let api=client(async()=>{calls++;return response(404,{error:'missing'});});
  await assert.rejects(api.get('/404'),/missing/);assert.equal(calls,1);
  calls=0;api=client(async()=>{calls++;return calls===1?response(503,{error:'busy'}):response(200,{ok:true});});
  assert.equal((await api.get('/retry')).ok,true);assert.equal(calls,2);
  calls=0;api=client((url,opts)=>new Promise((resolve,reject)=>{calls++;opts.signal.addEventListener('abort',()=>{const e=new Error();e.name='AbortError';reject(e);});}));
  await assert.rejects(api.get('/timeout',{timeout:5,retry:0}),e=>e.timeout===true);assert.equal(calls,1);
  calls=0;api=client(async()=>{calls++;return calls===1?response(200,{value:1}):response(500,{error:'offline'});});
  await api.get('/cached');await assert.rejects(api.get('/cached',{bypass:true,retry:0}),/offline/);
  const stale=await api.get('/cached',{bypass:true,retry:0,allowStale:true});
  assert.equal(stale.value,1);assert.ok(stale.cache_warning);assert.ok(stale.cached_at);
  let pending=[];api=client(()=>new Promise(resolve=>pending.push(resolve)));
  const first=api.get('/race');assert.equal(api.get('/race'),first);
  api.clear();const second=api.get('/race');assert.notEqual(first,second);
  pending[0](response(200,{value:'old'}));await first;
  assert.equal(api.get('/race'),second,'old finally must not remove the new request');
  pending[1](response(200,{value:'new'}));await second;
  assert.equal((await api.get('/race')).value,'new');
  calls=0;api=client(async()=>{calls++;return response(503,{error:'write failed'});});
  await assert.rejects(api.post('/write',{}),/write failed/);assert.equal(calls,1);
  console.log('HTTP client: 6 behavior groups passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
