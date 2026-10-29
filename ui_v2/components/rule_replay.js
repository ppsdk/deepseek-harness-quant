/* Classical-rule replay of the actual archived fills, without recomputing signals. */
(function(){
 'use strict';window.LW=window.LW||{};
 function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
 function num(v){return typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:4}):'—';}
 function pct(v){return typeof v==='number'&&Number.isFinite(v)?(v*100).toFixed(2)+'%':'—';}
 function table(headers,rows){return '<div class="bt-report-scroll"><table><thead><tr>'+headers.map(function(h){return '<th>'+esc(h)+'</th>';}).join('')+'</tr></thead><tbody>'+rows.map(function(row){return '<tr>'+row.map(function(v){return '<td>'+v+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div>';}
 function mount(host,r){
  var root=host.closest('.bt-report'),dates=r.dates||[],trades=r.trades||[],accounts=new Map((r.daily_account||[]).map(function(a){return [a.date,a];})),events=Array.from(new Set(trades.map(function(t){return t.date;}))).sort(),selected=null;
  host.className='bt-replay';host.dataset.ruleReplay='';
  host.innerHTML='<h3>逐日交易回放</h3><p class="bt-report-note">以下来自本次保存的实际成交与账户账本。仓位份额按成交逐笔重建；日内指标未在这里重新计算，可点击股票查看带成交标记的K线，并对照上方完整规则。</p><div class="bt-replay-toolbar"><label>回放日期 <input type="date" data-rule-date min="'+esc(dates[0])+'" max="'+esc(dates.at(-1))+'"></label><button data-rule-prev>上一个成交日</button><button data-rule-next>下一个成交日</button><label>成交日期 <select data-rule-event>'+events.map(function(d){return '<option>'+esc(d)+'</option>';}).join('')+'</select></label></div><p class="bt-report-note" data-rule-status></p><div class="bt-report-kpis" data-rule-account></div><h3>① 当天实际成交与原信号日</h3><div data-rule-fills></div><h3>② 成交后的持仓份额</h3><div data-rule-positions></div>';
  function $(s){return host.querySelector(s);}
  function codeLink(c,day){return '<button data-rule-code="'+esc(c)+'" data-rule-stock-day="'+esc(day)+'">'+esc(c)+' 买卖点</button>';}
  function select(day){
   if(!accounts.has(day))return;selected=day;var a=accounts.get(day),held={},cost={};
   trades.forEach(function(t){if(t.date>day)return;var before=held[t.code]||0;
    if(t.side==='buy')cost[t.code]=(before*(cost[t.code]||0)+t.amount+t.commission)/t.position_after;
    if(t.position_after>1e-8)held[t.code]=t.position_after;else{delete held[t.code];delete cost[t.code];}
   });
   var fills=trades.filter(function(t){return t.date===day;}),settings=r.report.settings;
   $('[data-rule-date]').value=day;$('[data-rule-event]').value=day;
   $('[data-rule-status]').textContent=day+' · '+fills.length+' 笔实际成交 · '+a.holding_count+' 只实际持仓';
   $('[data-rule-account]').innerHTML=[['账户总资产',num(a.equity)+' 元'],['现金',num(a.cash)+' 元'],['股票市值',num(a.market_value)+' 元'],['股票仓位',pct(a.exposure)]].map(function(v){return '<div>'+esc(v[0])+'<strong>'+esc(v[1])+'</strong></div>';}).join('');
   $('[data-rule-fills]').innerHTML=fills.length?table(['成交日／信号日','股票／K线','方向','退出或入场原因','成交份额','开盘→成交价','金额','佣金／税／滑点'],fills.map(function(t){return [esc(t.date)+'<br>'+esc(t.signal_date),codeLink(t.code,day),esc(settings.side_labels[t.side]||t.side),esc((settings.trigger_labels||{})[t.trigger]||t.trigger),num(t.quantity),num(t.open)+' → '+num(t.fill_price),num(t.amount),num(t.commission)+' / '+num(t.stamp_tax)+' / '+num(t.slippage_cost)];})):'<p class="bt-report-note">当天没有实际成交。普通交易间隔、是否有空位、入场和退出条件均可能影响交易；仅凭无成交不能推断唯一原因。</p>';
   var codes=Object.keys(held).sort();
   $('[data-rule-positions]').innerHTML=codes.length?table(['持仓／K线','实际份额','含买佣金的平均成本价'],codes.map(function(c){return [codeLink(c,day),num(held[c]),num(cost[c])];})):'<p class="bt-report-note">当天收盘为空仓。</p>';
   $('[data-rule-prev]').disabled=!events.some(function(d){return d<day;});$('[data-rule-next]').disabled=!events.some(function(d){return d>day;});
   host.dataset.selectedDate=day;
  }
  function onDate(e){select(e.detail.date);host.scrollIntoView({behavior:'smooth',block:'start'});}
  root.addEventListener('bt-select-date',onDate);
  $('[data-rule-date]').onchange=function(){select(this.value);};$('[data-rule-event]').onchange=function(){select(this.value);};
  $('[data-rule-prev]').onclick=function(){var day=events.findLast(function(d){return d<selected;});if(day)select(day);};
  $('[data-rule-next]').onclick=function(){var day=events.find(function(d){return d>selected;});if(day)select(day);};
  host.onclick=function(e){var b=e.target.closest('[data-rule-code]');if(b)root.dispatchEvent(new CustomEvent('bt-replay-stock',{detail:{code:b.dataset.ruleCode,date:b.dataset.ruleStockDay}}));};
  select(events.at(-1)||dates.at(-1));
  return function(){root.removeEventListener('bt-select-date',onDate);host.onclick=null;};
 }
 LW.ruleReplay={mount:mount};
})();
