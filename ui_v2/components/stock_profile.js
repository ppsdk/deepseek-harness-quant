/* 个股基本面面板：实时行情、公司概况、财务摘要、本机日线统计与 AI 基本面分析。
   字段名称、单位和来源都来自 /api/studio/stock/profile。 */
(function(){
 'use strict';window.LW=window.LW||{};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 var pct=function(v){return typeof v==='number'&&isFinite(v)?(v*100).toFixed(2)+'%':'—';};
 var current=null,sequence=0,timer=null;
 function tone(v){return typeof v==='number'?(v>0?' sp-up':v<0?' sp-down':''):'';}
 // AI 回复按纯文本显示，只把 **粗体** 和换行转成格式，不执行任何 HTML。
 function text(s){return esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/\n/g,'<br>');}
 function quote(q){
  if(!q)return '';var v=q.values,f=q.fields;
  return '<div class="sp-quote">'+q.tiles.map(function(k){var spec=f[k],val=v[k],shown=val==null?'—':(typeof val==='number'?val.toLocaleString('zh-CN',{maximumFractionDigits:2}):esc(val))+(spec.unit&&val!=null?' '+esc(spec.unit):'');return '<div class="sp-tile'+(spec.tone?tone(val):'')+'"><span>'+esc(spec.label)+'</span><b>'+shown+'</b></div>';}).join('')+'</div>'+
   '<p class="status-note">'+esc(q.source)+' · 行情时间 '+esc(v.time||'—')+(v.note?' · '+esc(v.note):'')+'</p>';
 }
 function local(l){
  if(!l||!l.available)return '<p class="status-note">'+esc(l?l.message:'')+'</p>';
  var r=l.returns||{};
  return '<div class="sp-quote">'+Object.keys(r).map(function(n){return '<div class="sp-tile'+tone(r[n])+'"><span>'+esc(n)+'日涨跌</span><b>'+pct(r[n])+'</b></div>';}).join('')+
   '<div class="sp-tile"><span>距一年高点</span><b>'+pct(l.from_high)+'</b></div><div class="sp-tile"><span>年化波动</span><b>'+pct(l.volatility)+'</b></div><div class="sp-tile"><span>20日均换手</span><b>'+(l.turnover==null?'—':l.turnover.toFixed(2)+'%')+'</b></div>'+
   Object.keys(l.above_ma||{}).map(function(n){return '<div class="sp-tile"><span>'+esc(n)+'日均线</span><b>'+(l.above_ma[n]?'在上方':'在下方')+'</b></div>';}).join('')+'</div><p class="status-note">'+esc(l.message)+'</p>';
 }
 function profile(p){
  if(!p)return '';var v=p.values;
  return '<dl class="sp-profile">'+Object.keys(v).filter(function(k){return v[k];}).map(function(k){return '<div'+(p.long_fields.indexOf(k)>=0?' class="sp-long"':'')+'><dt>'+esc(k)+'</dt><dd>'+esc(v[k])+'</dd></div>';}).join('')+'</dl><p class="status-note">'+esc(p.source)+' · 取得于 '+esc(p.fetched_at.slice(0,10))+'</p>';
 }
 function financial(f){
  if(!f||!f.rows.length)return '';
  return '<div class="research-scroll"><table class="research-table sp-fin"><thead><tr>'+f.columns.map(function(c){return '<th>'+esc(c)+'</th>';}).join('')+'</tr></thead><tbody>'+f.rows.map(function(r){return '<tr>'+f.columns.map(function(c){var v=r[c],cls=/同比|增长/.test(c)&&typeof v==='string'?(v.charAt(0)==='-'?' class="sp-down"':' class="sp-up"'):'';return '<td'+cls+'>'+esc(v==null?'—':v)+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div><p class="status-note">'+esc(f.source)+'。'+esc(f.note)+'</p>';
 }
 function ai(record){
  var box=document.querySelector('[data-sp-ai]');if(!box)return;
  if(timer){clearTimeout(timer);timer=null;}
  if(!record||!record.ai){box.innerHTML='<p class="status-note">还没有 AI 分析。点「AI 基本面分析」把上面的资料发给 HARNESS 主会话。</p>';return;}
  var a=record.ai,state={waiting:'等待回复…',partial:'正在写…',done:'已完成',timeout:'未取到回复',failed:'未发送'}[a.state]||a.state;
  box.innerHTML='<p class="status-note">'+esc(record.generated_at.replace('T',' ').slice(0,16))+' · '+esc(state)+(a.message&&!a.reply?' · '+esc(a.message):'')+'</p>'+(a.reply?'<div class="sp-ai-text">'+text(a.reply)+'</div>':'');
  if(a.state==='waiting'||a.state==='partial')timer=setTimeout(function(){LW.api.get('/api/studio/stock/ai?code='+encodeURIComponent(record.code),{bypass:true}).then(function(d){if(current===record.code)ai(d.record);}).catch(function(){});},5000);
 }
 function render(host,d){
  var errors=Object.keys(d.errors||{}).map(function(k){return d.errors[k];});
  host.innerHTML='<div class="research-actions"><b>'+esc(d.code)+' '+esc(d.name)+'</b><button class="lf-btn" data-sp-refresh>重新获取</button><button class="v2-btn" data-sp-ai-run>AI 基本面分析</button><a href="/control">在 AI 辅助页继续追问</a></div>'+
   (errors.length?'<p class="status-note sp-warn">'+errors.map(esc).join('；')+'（已显示能取到的部分）</p>':'')+
   '<h3 class="sp-h">实时行情</h3>'+quote(d.quote)+'<h3 class="sp-h">本机日线统计</h3>'+local(d.local)+
   (d.financial?'<h3 class="sp-h">财务摘要（最近'+d.financial.rows.length+'期）</h3>'+financial(d.financial):'')+
   (d.profile?'<details class="sp-details"><summary>公司概况</summary>'+profile(d.profile)+'</details>':'')+
   '<h3 class="sp-h">AI 基本面分析</h3><div data-sp-ai></div><p class="status-note">AI 分析只基于上面的资料，是研究讨论，不构成投资建议；数据来自第三方，仅在本机缓存。</p>';
  ai(d.ai);
  host.querySelector('[data-sp-refresh]').onclick=function(){load(host,d.code,true);};
  host.querySelector('[data-sp-ai-run]').onclick=function(){var b=this;b.disabled=true;host.querySelector('[data-sp-ai]').innerHTML='<p class="status-note">正在发送…</p>';LW.api.post('/api/studio/stock/ai',{code:d.code},{timeout:60000}).then(function(r){ai(r.record);}).catch(function(e){host.querySelector('[data-sp-ai]').innerHTML='<p class="status-note sp-warn">'+esc(e.message)+'</p>';}).finally(function(){b.disabled=false;});};
 }
 function load(host,code,refresh){
  var seq=++sequence;current=code;
  host.innerHTML='<p class="status-note">正在读取 '+esc(code)+' 的行情、概况和财务摘要…（首次需联网，之后按天缓存）</p>';
  return LW.api.get('/api/studio/stock/profile?'+new URLSearchParams({code:code,refresh:refresh?'1':'0'}),{bypass:true,timeout:45000,retry:0}).then(function(d){if(seq===sequence)render(host,d);}).catch(function(e){if(seq===sequence)host.innerHTML='<p class="status-note sp-warn">个股资料读取失败：'+esc(e.message)+'</p>';});
 }
 LW.stockProfile={show:function(host,code){if(typeof host==='string')host=document.getElementById(host);if(!host||!code)return;if(code===current&&host.querySelector('[data-sp-refresh]'))return;load(host,code,false);}};
})();
