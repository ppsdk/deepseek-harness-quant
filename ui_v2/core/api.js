/* Shared HTTP client: bounded requests, transient GET retries, explicit stale fallback. */
(function(win){
 'use strict';
 var cache=Object.create(null),inflight=Object.create(null),generation=0;
 function request(url,options,timeout){
  var controller=new AbortController(),timer=setTimeout(function(){controller.abort();},timeout);
  return fetch(url,Object.assign({cache:'no-store',signal:controller.signal},options||{})).then(function(r){
   return r.text().then(function(body){var data;try{data=body?JSON.parse(body):{};}catch(e){var bad=new Error('服务器返回了无法读取的数据（HTTP '+r.status+'）');bad.status=r.status;throw bad;}
    if(!r.ok){var error=new Error(data.error||data.message||'请求失败（HTTP '+r.status+'）');error.status=r.status;throw error;}return data;
   });
  }).catch(function(error){if(error.name==='AbortError'){var e=new Error('请求超时，请稍后重试；已提交的任务可在原页面查看状态。');e.timeout=true;throw e;}throw error;}).finally(function(){clearTimeout(timer);});
 }
 function get(url,opts){
  opts=opts||{};var old=cache[url],now=Date.now(),stamp=generation;
  if(!opts.bypass&&old&&now-old.ts<(opts.cacheMs==null?30000:opts.cacheMs))return Promise.resolve(old.data);
  if(inflight[url])return inflight[url];
  var retries=opts.retry==null?1:Math.max(0,opts.retry);
  function attempt(){return request(url,null,opts.timeout||8000).catch(function(error){if(retries>0&&(!error.status||error.status>=500||error.status===429)){retries--;return attempt();}throw error;});}
  var pending=attempt().then(function(data){if(generation===stamp)cache[url]={ts:Date.now(),data:data};return data;}).catch(function(error){
   if(opts.allowStale&&old&&old.data&&typeof old.data==='object')return Object.assign({},old.data,{cache_warning:'数据更新失败，正在显示之前取得的内容。',cached_at:new Date(old.ts).toISOString()});throw error;
  }).finally(function(){if(inflight[url]===pending)delete inflight[url];});
  inflight[url]=pending;return pending;
 }
 function post(url,body,opts){opts=opts||{};return request(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})},opts.timeout||30000);}
 function poll(url,seconds,render){var stopped=false,timer;function tick(){if(stopped)return;get(url).then(function(data){if(!stopped)render(data);}).catch(function(){}).finally(function(){if(!stopped)timer=setTimeout(tick,document.hidden?300000:seconds*1000);});}tick();return function(){stopped=true;clearTimeout(timer);};}
 function clear(){generation++;cache=Object.create(null);inflight=Object.create(null);}
 win.LW=win.LW||{};win.LW.api={get:get,post:post,poll:poll,clear:clear};
})(window);
