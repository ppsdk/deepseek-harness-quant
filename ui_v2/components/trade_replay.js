/* Frozen ledger explanations, loaded through the research API. */
(function(){
 'use strict';window.LW=window.LW||{};
 function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
 function num(v,n){return typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:n==null?2:n}):'—';}
 function pct(v){return typeof v==='number'&&Number.isFinite(v)?(v*100).toFixed(2)+'%':'—';}
 function table(headers,rows){return '<div class="bt-report-scroll"><table><thead><tr>'+headers.map(function(v){return '<th>'+esc(v)+'</th>';}).join('')+'</tr></thead><tbody>'+rows.map(function(row){return '<tr>'+row.map(function(v){return '<td>'+v+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div>';}
 function fitTable(data){return table(['预测年度／模型','训练截止','最后特征日','最后标签终点','训练样本','去相关→PCA','累计解释方差'],(data.fit_schedule||[]).map(function(f){return [esc(data.fit_schedule.length>1?f.prediction_year:'全期固定'),esc(f.fit_end),esc(f.last_feature_date),esc(f.last_outcome_date),num(f.samples,0),f.selected_dimension+' → '+f.output_dimension,pct(f.cumulative_explained_variance)];}));}
 function mount(host,result){
  var stopped=false,sequence=0,summary=null,selected=null,root=host.closest('.bt-report');
  host.className='bt-replay';host.innerHTML='<p class="bt-report-note">正在读取交易复盘证据…</p>';
  function $(s){return host.querySelector(s);}
  function stock(code,date){return '<button data-replay-code="'+esc(code)+'" data-replay-stock-date="'+esc(date)+'">'+esc(code)+'</button>';}
  function marketRow(m){if(!m)return '<p>没有前一交易日证据。</p>';return table(['信号收盘日','沪深300收盘','MA'+summary.market_config.fast,'MA'+summary.market_config.slow,'收盘 > 长均线','短均线 > 长均线','连续确认后门控'],[[esc(m.date),num(m.close),num(m.fast),num(m.slow),m.close_above_slow?'满足':'不满足',m.fast_above_slow?'满足':'不满足',m.gate===1?'允许持仓':m.gate===0?'要求退出':'历史不足']]);}
  function select(day){
   if(!summary||!summary.dates.includes(day))return Promise.resolve();
   selected=day;$('[data-replay-date]').value=day;
   var seq=++sequence;$('[data-replay-status]').textContent='正在读取 '+day+' 的原始交易和持仓…';
   return LW.api.get('/api/studio/trade-replay?'+new URLSearchParams({id:result.name,date:day})).then(function(response){
    if(stopped||seq!==sequence)return;
    if(!response.available)throw new Error(response.message||'复盘证据不可用');
    var d=response.detail,ui=summary.ui;
    $('[data-replay-status]').textContent=day+' · '+ui.status_labels[d.status]+' · 信号参考日 '+(d.signal_date||'—')+' · 当天 '+d.trade_count+' 笔实际成交';
    $('[data-replay-account]').innerHTML=[['收盘总资产',num(d.equity)+' 元'],['现金',num(d.cash)+' 元'],['股票仓位',pct(d.exposure)],['实际持仓',d.holding_count+' 只'],['当天净收益',pct(d.daily_return)],['距上一成交',d.gap_since_last_trade==null?'尚无成交':d.gap_since_last_trade+' 个交易日']].map(function(x){return '<div>'+esc(x[0])+'<strong>'+esc(x[1])+'</strong></div>';}).join('');
    $('[data-replay-market]').innerHTML=marketRow(d.signal_market)+'<p class="bt-report-note">以上是当日开盘前最近收盘的市场状态；图中收盘门控用于下一交易日。常规调仓日历：'+(d.regular_calendar_due?'本日为检查日':'本日非新一轮检查日')+'。延期订单仍可能成交，原信号日见逐笔记录。</p>';
    $('[data-replay-fills]').innerHTML=d.trades.length?table(['成交日／原信号日','股票／K线','方向与原因','模型原信号日排名','模型分数','调整份额','开盘价→成交价','成交金额','佣金／印花税／滑点'],d.trades.map(function(t){return [esc(t.date)+'<br>'+esc(t.signal_date),stock(t.code,t.date),esc(t.side==='buy'?'买入':'卖出')+'<br>'+esc(ui.trigger_labels[t.trigger]||t.trigger),t.signal_rank==null?'不在可评分候选中':num(t.signal_rank,0),num(t.model_score,6),num(t.quantity,4),num(t.open,4)+' → '+num(t.fill_price,4),num(t.amount),num(t.commission)+' / '+num(t.stamp_tax)+' / '+num(t.slippage_cost)];})):'<p class="bt-report-note">当天没有实际成交。可结合均线门控、距上次成交间隔及未成交记录查看；不据此推断唯一原因。</p>';
    $('[data-replay-positions]').innerHTML=d.positions.length?table(['收盘持仓／K线','昨日份额→今日份额','本日份额变化','收盘价','市值','账户权重','成本价','浮动盈亏','持有交易日'],d.positions.map(function(p){return [stock(p.code,day),num(p.quantity_before,4)+' → '+num(p.quantity,4),num(p.quantity_change,4),num(p.mark_price,4),num(p.market_value),pct(p.weight),num(p.cost_price,4),num(p.unrealized_pnl),num(p.holding_trading_days,0)];})):'<p class="bt-report-note">收盘为空仓，现金 '+num(d.cash)+' 元。已清仓股票见上方卖出明细。</p>';
    var retained=summary.features.filter(function(f){return d.fit_evidence?d.fit_evidence.selected_features.includes(f.key):f.retained;}),fit=d.fit_evidence;
    $('[data-replay-candidates]').innerHTML='<p class="bt-report-note">'+(fit?'该日使用的模型：训练截止 '+esc(fit.fit_end)+'，最后标签终点 '+esc(fit.last_outcome_date)+'；训练 '+fit.samples+' 行，'+fit.selected_dimension+' 个因子→PCA '+fit.output_dimension+'维，累计解释方差 '+pct(fit.cumulative_explained_variance)+'。<br>':'')+esc(d.signal_date)+'：可评分股票 '+d.eligible_score_count+' 只。列出分数靠前股票及相关旧持仓、成交股票；排名不是实际目标持仓。'+esc(ui.factor_note)+'</p>'+table(['排名','代码','预测分数','与本次成交的关系'].concat(retained.map(function(f){return f.label;})),d.candidates.map(function(c){return [c.rank==null?'—':num(c.rank,0),esc(c.code),num(c.score,6),esc([c.held_before?'原持仓':'',c.held_after?'收盘持有':'',c.bought?'当天买入':'',c.sold?'当天卖出':''].filter(Boolean).join(' · ')||'候选参考')].concat(retained.map(function(f){return pct(c.factors[f.key]);}));}));
    $('[data-replay-rejections]').innerHTML=d.rejections.length?table(['股票','方向','未成交原因'],d.rejections.map(function(t){return [esc(t.code),esc(t.side),esc(result.report.settings.rejection_reasons[t.reason]||t.reason)];})):'<p class="bt-report-note">当天没有记录未成交请求。</p>';
    var mode=$('[data-replay-events-only]').checked?summary.event_dates:summary.dates;
    $('[data-replay-prev]').disabled=!mode.some(function(x){return x<day;});$('[data-replay-next]').disabled=!mode.some(function(x){return x>day;});
    $('[data-replay-event]').value=day;host.dataset.selectedDate=day;
   }).catch(function(e){if(!stopped&&seq===sequence)$('[data-replay-status]').textContent='复盘未打开：'+e.message;});
  }
  function onSelect(e){if(summary){select(e.detail.date);host.scrollIntoView({behavior:'smooth',block:'start'});}}
  root.addEventListener('bt-select-date',onSelect);
  host.onclick=function(e){var b=e.target.closest('[data-replay-code]');if(b)root.dispatchEvent(new CustomEvent('bt-replay-stock',{detail:{code:b.dataset.replayCode,date:b.dataset.replayStockDate}}));};
  LW.api.get('/api/studio/trade-replay?id='+encodeURIComponent(result.name)).then(function(data){
   if(stopped)return;
   if(!data.available){host.innerHTML='<p class="bt-report-note">'+esc(data.message)+'</p>';return;}
   summary=data;var ui=data.ui;
   host.innerHTML='<h3>'+esc(ui.title)+'</h3><p class="bt-report-note">'+esc(ui.note)+'</p><p><strong>'+esc(data.title)+'</strong></p><p class="bt-report-note">'+esc(data.description[0].text)+'</p><details class="bt-report-audit" data-strategy-description><summary>展开详细策略说明、因子与模型参数</summary>'+data.description.slice(1).map(function(x){return '<p class="bt-report-note">'+esc(x.text)+'</p>';}).join('')+table(['输入特征','计算方式／参数','训练期筛选状态'],data.features.map(function(f){return [esc(f.label),esc(f.formula)+' · '+(f.definition.sign===-1?'取负后做截面排名':'原值做截面排名'),f.retained?(data.fit_schedule.length>1?'曾保留，逐日见候选表':'保留后进入PCA'):'训练期去相关剔除'];}))+'<p class="bt-report-note">模型参数：'+esc(JSON.stringify(data.model_params))+'<br>最近一次训练 '+data.fit.samples+' 行；最后特征日 '+esc(data.fit.last_feature_date)+'；最后标签终点 '+esc(data.fit.last_outcome_date)+'；单个预测窗口内重拟合：'+(data.fit.validation_refit?'是':'否')+'</p>'+fitTable(data)+'</details><h3>指数均线与收盘门控</h3><div data-replay-index-chart></div><div class="bt-replay-toolbar"><label>复盘日期 <input type="date" data-replay-date min="'+esc(data.dates[0])+'" max="'+esc(data.dates.at(-1))+'"></label><button data-replay-prev>上一个日期</button><button data-replay-next>下一个日期</button><label><input type="checkbox" data-replay-events-only checked> 前后跳转只看实际成交日</label><label>直接跳到成交日 <select data-replay-event></select></label></div><p class="bt-report-note" role="status" data-replay-status></p><div class="bt-report-kpis" data-replay-account></div><h3>① 开盘前可见的市场信号</h3><div data-replay-market></div><h3>② 当天实际怎么成交</h3><p class="bt-report-note">'+esc(ui.signal_note)+'</p><div data-replay-fills></div><h3>③ 成交后的实际持仓与现金</h3><p class="bt-report-note">'+esc(ui.holdings_note)+'</p><div data-replay-positions></div><details data-replay-ranking><summary>④ 查看候选排名与实际因子输入</summary><div data-replay-candidates></div></details><details><summary>⑤ 当天未成交记录</summary><div data-replay-rejections></div></details>';
   $('[data-replay-event]').innerHTML=data.daily.filter(function(d){return d.trade_count;}).map(function(d){return '<option value="'+esc(d.date)+'">'+esc(d.date)+' · '+esc(ui.status_labels[d.status])+' · '+d.trade_count+' 笔</option>';}).join('');
   $('[data-replay-date]').onchange=function(){select(this.value);};$('[data-replay-event]').onchange=function(){select(this.value);};
   function move(direction){var mode=$('[data-replay-events-only]').checked?data.event_dates:data.dates,next=direction>0?mode.find(function(d){return d>selected;}):mode.findLast(function(d){return d<selected;});if(next){$('[data-replay-event]').value=next;select(next);}}
   $('[data-replay-prev]').onclick=function(){move(-1);};$('[data-replay-next]').onclick=function(){move(1);};$('[data-replay-events-only]').onchange=function(){if(selected)select(selected);};
   var s=result.report.settings.series,market=data.market_config;
   LW.backtestReport.chart($('[data-replay-index-chart]'),data.dates,[{label:market.code+' 收盘及本轮实际均线',series:[{label:'指数收盘',color:s.nav.color,values:data.daily.map(function(d){return d.market.close;})},{label:'MA'+market.fast,color:s.bench_nav.color,values:data.daily.map(function(d){return d.market.fast;})},{label:'MA'+market.slow,color:s.excess_nav.color,values:data.daily.map(function(d){return d.market.slow;})}]},{label:'当日收盘门控：1允许，0退出（下一交易日使用）',unit:'exposure',series:[{label:'收盘确认门控',color:s.exposure.color,values:data.daily.map(function(d){return d.market.gate;})}]}],{onSelect:function(day){select(day);}});
   var initial=data.event_dates.at(-1)||data.dates[0];$('[data-replay-event]').value=initial;select(initial);
  }).catch(function(e){if(!stopped)host.innerHTML='<p class="bt-report-note">交易复盘证据未打开：'+esc(e.message)+'</p>';});
  return function(){stopped=true;sequence++;root.removeEventListener('bt-select-date',onSelect);var chart=$('[data-replay-index-chart]');if(chart&&chart._plotCleanup)chart._plotCleanup();};
 }
 LW.tradeReplay={mount:mount};
})();
