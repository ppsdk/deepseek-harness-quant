/* 每日大盘分析：五维度规则打分 + 关键数据 + AI 日报。数据全部来自 /api/studio/market/analysis。 */
(function(){
 'use strict';window.LW=window.LW||{};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 var pct=function(v,d){return typeof v==='number'&&isFinite(v)?(v*100).toFixed(d==null?1:d)+'%':'—';};
 var num=function(v,d){return typeof v==='number'&&isFinite(v)?v.toFixed(d==null?1:d):'—';};
 function text(s){return esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/【(.+?)】/g,'<b class="ma-sec">【$1】</b>').replace(/\n/g,'<br>');}
 LW.marketAnalysis={mount:function(host){
  if(typeof host==='string')host=document.getElementById(host);if(!host)return;
  var timer=null,shown=null;
  function bar(p){var s=p.score;return '<div class="ma-part"><span>'+esc(p.label)+'</span><i><b style="width:'+(s==null?0:Math.max(2,s))+'%"></b></i><em>'+(s==null?'缺数据':num(s,0))+'</em><small>权重 '+num(p.weight*100,0)+'%</small></div>';}
  function indices(list){return list.filter(function(i){return i.available;}).map(function(i){var ma=Object.keys(i.above||{});return '<tr><td>'+esc(i.name)+'<br><small>'+esc(i.date)+'</small></td><td>'+num(i.close,2)+'</td>'+Object.keys((i.returns||{})).map(function(n){var v=i.returns[n];return '<td class="'+(v>0?'sp-up':v<0?'sp-down':'')+'">'+pct(v,2)+'</td>';}).join('')+'<td>'+ma.filter(function(k){return i.above[k];}).length+' / '+ma.length+'<br><small>'+(i.bull_alignment?'多头排列':i.bear_alignment?'空头排列':'交错')+'</small></td><td>'+pct(i.from_high)+'</td></tr>';}).join('');}
  function render(d){
   var r=d.record;if(timer){clearTimeout(timer);timer=null;}
   var days=d.history||[],cap=d.capabilities||{};
   var head='<div class="research-actions">'+(cap.ai?'<button class="v2-btn" data-ma-ai>AI 分析云端大盘</button>':'')+'<button class="studio-button" data-ma-run>获取云端数据</button><button class="studio-button" data-ma-refresh>刷新云端分析</button>'+(days.length>1?'<label class="mini">历史 <select data-ma-day>'+days.map(function(h){return '<option value="'+esc(h.date)+'"'+(r&&h.date===r.date?' selected':'')+'>'+esc(h.date)+' · '+esc(h.state)+' '+num(h.total,0)+'</option>';}).join('')+'</select></label>':'')+'</div><p class="status-note" role="status" data-ma-status></p>';
   if(!r){host.innerHTML=head+'<p class="status-note">云端尚未返回大盘分析记录。当前页面只展示云端结果，不使用本地个股库计算。</p>';bind(null);return;}
   if(r.view==='online_quotes'){
    var columns=r.index_columns||[];
    host.innerHTML=head+'<p class="status-note">'+esc(r.date)+' · '+(r.sources||[]).map(esc).join(' / ')+' · 获取时间 '+esc(r.generated_at)+'</p><div class="studio-scroll"><table class="studio-table"><thead><tr>'+columns.map(function(c){return '<th>'+esc(c.label)+'</th>';}).join('')+'</tr></thead><tbody>'+(r.indices||[]).map(function(row){return '<tr>'+columns.map(function(c){var v=row[c.key];return '<td>'+esc(c.format==='percent'?pct(v,2):c.format==='number'?num(v,2):v==null?'—':v)+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div><p class="status-note">'+(r.notes||[]).map(esc).join(' ')+'</p>'+((r.warnings||[]).length?'<p class="status-note sp-warn">'+r.warnings.map(esc).join('；')+'</p>':'');
    bind(r);return;
   }
   var s=Object.assign({total:null,state:'云端未提供评分',hint:'',parts:{},missing:[]},r.scores||{}),b=Object.assign({},(r.breadth||{}).latest||{}),m=r.market||{},hist=(r.breadth||{}).history||[];r.indices=r.indices||[];
   host.innerHTML=head+
    '<div class="ma-top"><div class="ma-score"><span>'+esc(r.date)+' 云端评分</span><strong>'+num(s.total,0)+'</strong><b>'+esc(s.state)+'</b><p>'+esc(s.hint)+'</p>'+(r.date!==String(new Date().toISOString().slice(0,10))?'<p class="status-note sp-warn">分析日期 '+esc(r.date)+'，不是今天。按云端实际返回日期展示；可刷新云端数据，不读取本地个股日线。</p>':'')+'</div><div class="ma-parts">'+Object.keys(s.parts).map(function(k){return bar(s.parts[k]);}).join('')+(s.missing.length?'<p class="status-note">缺数据未计入：'+s.missing.map(esc).join('、')+'</p>':'')+'</div></div>'+
    '<div class="sp-quote">'+[['上涨 / 下跌',num(b.up,0)+' / '+num(b.down,0)],['上涨占比',pct(b.up_ratio)],['站上20日线',pct(b.above_ma20)],['站上60日线',pct(b.above_ma60)],['60日新高 / 新低',num(b.new_high,0)+' / '+num(b.new_low,0)],['涨停 / 跌停（近似）',num(b.limit_up,0)+' / '+num(b.limit_down,0)],['成交额',num(b.amount_yi,0)+' 亿'],['相对20日均额',num(b.amount_ratio,2)+' 倍']].map(function(x){return '<div class="sp-tile"><span>'+esc(x[0])+'</span><b>'+esc(x[1])+'</b></div>';}).join('')+'</div>'+
    '<p class="status-note">云端统计 '+num(b.stocks,0)+' 只个股；缺失字段以 — 显示。'+esc((r.breadth||{}).note||'')+'</p>'+
    '<div class="research-scroll"><table class="research-table"><thead><tr><th>指数</th><th>收盘</th>'+Object.keys((r.indices[0]||{}).returns||{}).map(function(n){return '<th>'+esc(n)+'日</th>';}).join('')+'<th>站上均线</th><th>距一年高点</th></tr></thead><tbody>'+indices(r.indices)+'</tbody></table></div>'+
    '<details class="sp-details"><summary>宽度近'+hist.length+'日变化、板块与择时规则</summary><div class="research-scroll"><table class="research-table"><thead><tr><th>日期</th><th>上涨占比</th><th>站上20日线</th><th>新高/新低</th><th>涨停/跌停</th><th>成交额（亿）</th></tr></thead><tbody>'+hist.slice().reverse().map(function(h){return '<tr><td>'+esc(h.date)+'</td><td>'+pct(h.up_ratio)+'</td><td>'+pct(h.above_ma20)+'</td><td>'+h.new_high+' / '+h.new_low+'</td><td>'+h.limit_up+' / '+h.limit_down+'</td><td>'+num(h.amount_yi,0)+'</td></tr>';}).join('')+'</tbody></table></div>'+
     '<p class="status-note">最热板块：'+(m.sectors_hot||[]).map(function(x){return esc(x.name)+' '+num(x.temperature,0);}).join('、')+' · 最冷：'+(m.sectors_cold||[]).map(function(x){return esc(x.name)+' '+num(x.temperature,0);}).join('、')+'</p>'+
     '<p class="status-note">恐慌指标：'+(m.fear||[]).map(function(f){return esc(f.name.split('：').pop())+' '+num(f.value,2)+'（历史位置 '+num(f.percentile,0)+'）'+(f.stale?' 过旧':'');}).join('；')+'</p>'+
     '<p class="status-note">择时规则：'+(r.timing||[]).map(function(t){return esc(t.rule)+' '+(t.allow==null?'—':t.allow?'✓':'✗');}).join('；')+'</p>'+
     '<p class="status-note">'+(r.notes||[]).map(esc).join(' ')+'</p></details>'+
    '<h3 class="sp-h">AI 大盘日报</h3><div data-ma-reply>'+reply(r.ai)+'</div>';
   bind(r);
   if(r.ai&&(r.ai.state==='waiting'||r.ai.state==='partial'))timer=setTimeout(function(){load(r.date);},5000);
  }
  function reply(a){
   if(!a)return '<p class="status-note">这一天还没有 AI 日报。点「AI 分析今日大盘」生成；AI 按趋势、宽度、情绪周期、量能、板块轮动、股债性价比和云端择时规则的顺序复盘，给出研究口径的仓位区间和明日观察清单。</p>';
   var st={waiting:'已发送，等待回复…',partial:'正在写…',done:'已完成',timeout:'未取到回复',failed:'未发送'}[a.state]||a.state;
   return '<p class="status-note">'+esc((a.sent_at||'').replace('T',' ').slice(0,16))+' · '+esc(st)+(a.message&&!a.reply?' · '+esc(a.message):'')+'</p>'+(a.reply?'<div class="sp-ai-text">'+text(a.reply)+'</div>':'')+'<p class="status-note">AI 日报只基于云端提供的数据，是研究讨论，不构成投资建议；完整对话在「AI 辅助」页的主会话。</p>';
  }
  function bind(r){
   function post(body,label){var status=host.querySelector('[data-ma-status]');host.querySelectorAll('button').forEach(function(b){b.disabled=true;});status.textContent=label;
    LW.api.post('/api/studio/market/analysis',body,{timeout:180000}).then(function(d){render(d);host.querySelector('[data-ma-status]').textContent=d.message;}).catch(function(e){status.textContent='未完成：'+e.message;host.querySelectorAll('button').forEach(function(b){b.disabled=false;});});}
   var ai=host.querySelector('[data-ma-ai]');if(ai)ai.onclick=function(){post({ai:true},'正在请求云端 AI 分析…');};
   host.querySelector('[data-ma-run]').onclick=function(){post({},'正在获取云端数据…');};
   host.querySelector('[data-ma-refresh]').onclick=function(){post({refresh:true},'正在刷新云端分析…');};
   var day=host.querySelector('[data-ma-day]');if(day)day.onchange=function(){load(this.value);};
  }
  function load(day){shown=day||null;return LW.api.get('/api/studio/market/analysis'+(day?'?date='+encodeURIComponent(day):''),{bypass:true,timeout:15000}).then(render).catch(function(e){host.innerHTML='<p class="status-note sp-warn">大盘分析未加载：'+esc(e.message)+'</p>';});}
  load(null);
 }};
})();
