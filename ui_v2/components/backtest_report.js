/* One report for strategy, ML and archived runs. All ledger data comes from APIs. */
(function(){
 'use strict';window.LW=window.LW||{};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 var number=function(v){return typeof v==='number'&&Number.isFinite(v);};
 var pct=function(v){return number(v)?(100*v).toFixed(2)+'%':'—';};
 var money=function(v){return number(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'—';};
 function table(headers,rows,cls){return '<div class="bt-report-scroll '+(cls||'')+'"><table><thead><tr>'+headers.map(function(v){return '<th>'+esc(v)+'</th>';}).join('')+'</tr></thead><tbody>'+rows.map(function(row){return '<tr>'+row.map(function(v){return '<td>'+v+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div>';}
 function csvDownload(name,columns,items){
  function cell(v){if(typeof v==='string'&&/^[=+@\-]/.test(v))v="'"+v;return '"'+String(v==null?'':v).replace(/"/g,'""')+'"';}
  var rows=[Object.values(columns)].concat(items.map(function(item){return Object.keys(columns).map(function(k){return item[k];});}));
  var url=URL.createObjectURL(new Blob(['\ufeff'+rows.map(function(row){return row.map(cell).join(',');}).join('\r\n')],{type:'text/csv;charset=utf-8;'}));
  var a=document.createElement('a');a.href=url;a.download=name+'.csv';a.click();setTimeout(function(){URL.revokeObjectURL(url);},1000);
 }
 function chart(host,dates,lanes,options){
  options=options||{};
  if(host._plotCleanup)host._plotCleanup();host.classList.add('bt-plot');
  if(!dates.length){host.textContent='没有可显示的净值数据。';return;}
  host.innerHTML='<div class="bt-report-tools"><label>从 <input type="date" data-from></label><label>到 <input type="date" data-to></label><button data-action="in">放大</button><button data-action="out">缩小</button><button data-action="reset">完整区间</button><button data-action="full">放大查看</button></div><div class="bt-plot-legend"></div><div class="bt-plot-info" aria-live="off">悬停看数值，滚轮缩放，按住拖动。拖动图表右下角可调整高度。</div><div class="bt-plot-surface"></div>';
  var box=host.querySelector('.bt-plot-surface'),info=host.querySelector('.bt-plot-info'),from=host.querySelector('[data-from]'),to=host.querySelector('[data-to]');
  if((options.events||[]).length)info.textContent+=' 红点买入、绿点卖出；点击日期可复盘。';
  var start=0,end=dates.length-1,drag=null,lastHover=null;
  from.min=to.min=dates[0];from.max=to.max=dates[dates.length-1];
  host.querySelector('.bt-plot-legend').innerHTML=lanes.flatMap(function(l){return l.series;}).map(function(s){return '<span style="--series-color:'+esc(s.color)+'">'+esc(s.label)+'</span>';}).join('');
  function bounds(){start=Math.max(0,Math.min(start,dates.length-1));end=Math.max(start,Math.min(end,dates.length-1));from.value=dates[start];to.value=dates[end];}
  function draw(){
   bounds();var W=Math.max(240,box.clientWidth),H=Math.max(300,box.clientHeight),left=65,right=18,top=25,foot=28,gap=28,laneH=(H-top-foot-gap*(lanes.length-1))/lanes.length,body='';
   var x=function(i){return left+(i-start)/Math.max(1,end-start)*(W-left-right);};
   lanes.forEach(function(l,lane){
    var vals=l.series.flatMap(function(s){return s.values.slice(start,end+1).filter(number);}),lo=vals.length?Math.min.apply(null,vals):0,hi=vals.length?Math.max.apply(null,vals):1;
    if(l.zero){lo=Math.min(lo,0);hi=Math.max(hi,0);}if(l.unit==='exposure'){lo=0;hi=Math.max(1,hi);}if(hi===lo){lo-=.01;hi+=.01;}
    var y0=top+lane*(laneH+gap),plotH=laneH-18,y=function(v){return y0+(hi-v)/(hi-lo)*plotH;};
    body+='<text x="'+left+'" y="'+(y0-8)+'" fill="var(--bt-lane-lbl,#b6c6dd)" font-size="12">'+esc(l.label)+'</text>';
    for(var g=0;g<4;g++){var value=hi-g/3*(hi-lo),gy=y(value);body+='<line x1="'+left+'" x2="'+(W-right)+'" y1="'+gy+'" y2="'+gy+'" stroke="var(--bt-grid,#28354a)"/><text x="'+(left-7)+'" y="'+(gy+4)+'" text-anchor="end" fill="var(--bt-axis,#a2b5cf)" font-size="10">'+esc(l.percent?(value*100).toFixed(1)+'%':value.toFixed(2))+'</text>';}
    l.series.forEach(function(s){var path='',open=false;for(var i=start;i<=end;i++){var v=s.values[i];if(!number(v)){open=false;continue;}path+=(open?' L':' M')+x(i).toFixed(2)+','+y(v).toFixed(2);open=true;if(start===end)path+=' l1,0';}body+='<path d="'+path+'" fill="none" stroke="'+esc(s.color)+'" stroke-width="1.8" vector-effect="non-scaling-stroke"/>';});
    if(lane===0&&l.series.length)(options.events||[]).forEach(function(event){var i=dates.indexOf(event.date),v=l.series[0].values[i];if(i<start||i>end||!number(v))return;body+='<circle class="bt-trade-event" data-date="'+esc(event.date)+'" data-side="'+esc(event.side)+'" cx="'+x(i)+'" cy="'+(y(v)+(event.side==='buy'?-4:4))+'" r="4" fill="'+(event.side==='buy'?'#ff9792':'#6eddb6')+'"><title>'+esc(event.date+' · '+event.label)+'</title></circle>';});
   });
   (W<600?[0,1]:[0,.25,.5,.75,1]).forEach(function(f){var i=Math.round(start+(end-start)*f);body+='<text x="'+x(i)+'" y="'+(H-5)+'" text-anchor="'+(f===0?'start':f===1?'end':'middle')+'" fill="var(--bt-date,#9bafcb)" font-size="10">'+esc(dates[i])+'</text>';});
   box.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="交互回测图，净值、回撤与仓位按日期对齐">'+body+'<line data-cross x1="0" x2="0" y1="12" y2="'+(H-20)+'" stroke="var(--bt-cross,#bbcce7)" stroke-dasharray="3 3" visibility="hidden"/></svg>';
   if(lastHover!=null)hover(lastHover);
  }
  function hover(i){i=Math.max(start,Math.min(end,i));lastHover=i;var line=box.querySelector('[data-cross]'),W=Math.max(240,box.clientWidth),x=65+(i-start)/Math.max(1,end-start)*(W-83);line.setAttribute('x1',x);line.setAttribute('x2',x);line.setAttribute('visibility','visible');info.textContent=dates[i]+' · '+lanes.flatMap(function(l){return l.series.map(function(s){var v=s.values[i];return s.label+' '+(l.percent?pct(v):number(v)?v.toFixed(4):'—');});}).join(' · ');}
  function zoom(factor,anchor){var length=Math.max(1,end-start),n=Math.max(Math.min(2,dates.length-1),Math.min(dates.length-1,Math.round(length*factor))),center=anchor==null?(start+end)/2:anchor;start=Math.round(center-n/2);start=Math.max(0,Math.min(start,dates.length-1-n));end=start+n;draw();}
  function dayIndex(clientX){var r=box.getBoundingClientRect();return Math.round(start+Math.max(0,Math.min(1,(clientX-r.left-65)/Math.max(1,r.width-83)))*(end-start));}
  box.addEventListener('wheel',function(e){e.preventDefault();zoom(e.deltaY>0?1.3:.75,dayIndex(e.clientX));},{passive:false});
  box.onpointerdown=function(e){if(e.button!==0)return;var r=box.getBoundingClientRect();if(e.clientX>r.right-20&&e.clientY>r.bottom-20)return;drag={x:e.clientX,start:start,end:end};box.setPointerCapture(e.pointerId);};
  box.onpointermove=function(e){if(drag){var move=Math.round((drag.x-e.clientX)/Math.max(1,box.clientWidth-83)*(drag.end-drag.start)),span=drag.end-drag.start;start=Math.max(0,Math.min(dates.length-1-span,drag.start+move));end=start+span;draw();}else hover(dayIndex(e.clientX));};
  box.onpointerup=function(e){var clicked=drag&&Math.abs(e.clientX-drag.x)<5;drag=null;if(clicked&&options.onSelect)options.onSelect(e.target.dataset.date||dates[dayIndex(e.clientX)]);};box.onpointercancel=function(){drag=null;};
  from.onchange=to.onchange=function(){var a=dates.findIndex(function(d){return d>=from.value;}),b=dates.findLastIndex(function(d){return d<=to.value;});if(a<0||b<a){info.textContent='请填写有效的起止日期。';bounds();return;}start=a;end=b;draw();};
  host.querySelector('.bt-report-tools').onclick=function(e){var a=e.target.dataset.action;if(a==='in')zoom(.7);if(a==='out')zoom(1.4);if(a==='reset'){start=0;end=dates.length-1;draw();}if(a==='full'){host.classList.toggle('bt-plot-full');e.target.textContent=host.classList.contains('bt-plot-full')?'退出放大（Esc）':'放大查看';draw();}};
  function onEscape(e){if(e.key==='Escape'&&host.classList.contains('bt-plot-full')){host.classList.remove('bt-plot-full');host.querySelector('[data-action="full"]').textContent='放大查看';draw();}}
  document.addEventListener('keydown',onEscape);var observer=new ResizeObserver(draw);observer.observe(box);draw();
  host._plotCleanup=function(){observer.disconnect();document.removeEventListener('keydown',onEscape);host.classList.remove('bt-plot-full');};return host._plotCleanup;
 }
 function render(host,r){
  if(typeof host==='string')host=document.getElementById(host);if(host._reportCleanup)host._reportCleanup();
  var report=r.report,cfg=report&&report.settings;if(!cfg){host.textContent='这份结果没有报告数据，请在回测管理中重新打开。';return;}
  if(r.benchmark){
   cfg=Object.assign({},cfg,{metrics:Object.assign({},cfg.metrics),series:JSON.parse(JSON.stringify(cfg.series))});
   Object.keys(cfg.benchmark_metrics||{}).forEach(function(key){if(Object.prototype.hasOwnProperty.call(r.metrics||{},key))cfg.metrics[key]=cfg.benchmark_metrics[key];});
   Object.keys(r.benchmark.series_labels||{}).forEach(function(key){if(cfg.series[key])cfg.series[key].label=r.benchmark.series_labels[key];});
  }
  var cleanups=[],metrics=r.metrics||{},dates=r.dates||[],columns=cfg.trade_columns||{};
  host.classList.add('bt-report');host.classList.remove('studio-note');host.innerHTML='<h3>'+esc(r.title||r.name)+'</h3><p class="bt-report-note">'+esc(r.params.start)+' 至 '+esc(r.params.end)+' · '+esc(r.generated_at||'')+' · 初始资金 '+money(r.params.initial_capital)+' 元 · 选取 '+(r.data.selected_codes||[]).length+' 只股票</p><div class="bt-report-kpis">'+Object.keys(cfg.metrics).map(function(k){var m=cfg.metrics[k],v=metrics[k];return '<div>'+esc(m.label)+'<strong>'+(m.format==='percent'?pct(v):number(v)?v.toFixed(2):'—')+'</strong></div>';}).join('')+'</div><p class="bt-report-note">年化收益把这段表现折算到一年；最大回撤表示从此前高点最多跌了多少。月度统计包含未满整月的首尾月份。</p><div class="bt-report-tools"><label>主图 <select data-mode><option value="nav">净值与等权参考</option><option value="relative">策略相对等权参考</option></select></label><a href="'+esc(r.archive_url||'/backtests')+'">下载完整 JSON</a><button data-export="nav">导出每日净值 CSV</button></div><div data-plot></div><p class="bt-report-note" data-dd></p><p class="bt-report-note" data-account></p><h3>在K线上看策略买卖点</h3><p class="bt-report-note">选择这次回测实际交易过的股票，红色标记是买入，绿色标记是卖出。点击成交表中的股票也可以切换。仅产生选股信号但没有成交的记录不会标成买入。</p><div class="bt-report-tools"><label>股票 <select data-fill-code></select></label><label>K线周期 <select data-fill-timeframe><option value="day">日线</option><option value="week">周线</option><option value="month">月线</option></select></label><label>图表组合 <select data-study-preset></select></label><a data-fill-link>在研究工作台打开</a></div><div class="studio-controls indicator-picks bt-fill-indicators" data-fill-indicators></div><p class="bt-report-note" data-fill-status></p><div data-fill-chart></div><h3>每月赚了多少</h3><div data-months></div><div class="bt-report-grid"><section><h3>各年收益</h3><div data-years></div></section><section><h3>日收益分布</h3><div data-hist></div></section></div><div class="bt-report-grid"><section><h3>费用与滑点</h3><div data-costs></div></section><section><h3>为什么没有成交</h3><div data-reasons></div></section></div><details class="bt-report-audit" open><summary>回测假设与尚未模拟的规则</summary><p class="bt-report-note">等权参考是本次股票范围的平均日收益，未扣费用，并非沪深300等可交易指数。相对表现是两条净值相除，不是另一套可独立交易的收益。图表不能替代交易规则核验。</p><ul>'+((r.warnings||[]).map(function(s){return '<li>'+esc(s)+'</li>';}).join(''))+'</ul></details><h3>完整成交明细</h3><div class="bt-report-tools"><label>股票代码 <input data-trade-q placeholder="输入代码筛选"></label><label>方向 <select data-trade-side><option value="">全部</option>'+Object.keys(cfg.side_labels).map(function(k){return '<option value="'+esc(k)+'">'+esc(cfg.side_labels[k])+'</option>';}).join('')+'</select></label><button data-export="trades">导出全部成交 CSV</button></div><div data-trades></div><div class="bt-report-tools"><button data-page="prev">上一页</button><span data-page-info></span><button data-page="next">下一页</button></div><details><summary>逐次未成交记录（'+(r.rejections||[]).length+' 次）</summary><div class="bt-report-tools"><button data-export="rejections">导出全部未成交 CSV</button></div><div data-rejected></div></details>';
  var $=function(s){return host.querySelector(s);};
  if(r.benchmark){
   if(r.benchmark.nav_mode_label)$('[data-mode] option[value="nav"]').textContent=r.benchmark.nav_mode_label;
   if(r.benchmark.relative_mode_label)$('[data-mode] option[value="relative"]').textContent=r.benchmark.relative_mode_label;
   if(r.benchmark.description)host.querySelector('.bt-report-audit .bt-report-note').textContent=r.benchmark.description;
  }
  // ── 因子得分分项条形图 ─────────────────────────────────────────────────────
  (function(){
   var factors=r.factors||[];if(!factors.length)return;
   var box=document.createElement('div');box.className='bt-report-factor-bars';
   box.innerHTML='<h3>因子权重构成</h3><div class="bt-factor-bar-list">'+
    factors.map(function(f){
     var w=typeof f.weight==='number'?f.weight:1;
     var sign=f.sign>0?'↑高优先':'↓低优先';
     var bar=Math.min(100,Math.round(w/Math.max.apply(null,factors.map(function(x){return x.weight||1;}))*100));
     return '<div class="bt-factor-row">'+
      '<span class="bt-factor-name" title="'+esc(f.name)+'">'+esc(f.label||f.name)+'</span>'+
      '<span class="bt-factor-bar-wrap"><i class="bt-factor-bar-fill" style="width:'+bar+'%"></i></span>'+
      '<span class="bt-factor-meta">权重 '+w.toFixed(1)+' · '+sign+'</span>'+
     '</div>';
    }).join('')+
   '</div>';
   host.querySelector('.bt-report-kpis').insertAdjacentElement('afterend',box);
  })();
  var settingsBox=document.createElement('details');settingsBox.className='bt-report-audit';
  var rerun=new URLSearchParams({strategy:r.strategy});Object.keys(r.params||{}).forEach(function(k){rerun.set(k,typeof r.params[k]==='object'?JSON.stringify(r.params[k]):r.params[k]);});
  settingsBox.innerHTML='<summary>本次参数、仓位规则与期末持仓</summary><p class="bt-report-note">'+esc(cfg.position_note||'')+'</p><p><a href="/backtest?'+esc(rerun.toString())+'">用这次参数重新设置回测</a> · <a href="/positions?run='+encodeURIComponent(r.name)+'">打开仓位管理</a></p>'+table(['参数','本次设置'],(report.parameters||[]).map(function(p){return [esc(p.label),esc(p.value)];}))+(report.indicator_parameters||[]).map(function(g){return '<p class="bt-report-note">'+esc(g.label)+'：'+g.parameters.map(function(p){return esc(p.label)+' = '+esc(p.value);}).join('；')+'</p>';}).join('')+(report.position_detail_available?table(['期末股票','调整份额','成本价（含买佣金）','期末估值价','仓位','浮动盈亏（元）'],(report.positions||[]).map(function(p){return [esc(p.code),money(p.quantity),money(p.cost_price),money(p.mark_price),pct(p.weight),money(p.unrealized_pnl)];})):'<p class="bt-report-note">旧实验没有逐股成本与期末估值，详情未记录。</p>')+'<p class="bt-report-note">'+(report.provenance?'实现摘要 '+esc(report.provenance.source_hash.slice(0,16))+' · Python '+esc(report.provenance.python)+' · '+Object.keys(report.provenance.dependencies).map(function(k){return esc(k)+' '+esc(report.provenance.dependencies[k]);}).join(' / '):'旧实验未记录实现和依赖版本。')+'</p>';
  if(r.signal_plan)settingsBox.insertAdjacentHTML('beforeend','<p class="bt-report-note">候选股信号：'+esc(r.signal_plan.name)+'<br>入场：'+esc(r.signal_plan.entry.formula)+'<br>退出：'+esc(r.signal_plan.exit.formula)+'<br>有持仓先检查退出；无持仓在条件满足时才尝试入场，期间不因普通换股日卖出。原选股规则用于有空位时的候选排序。</p>');
  if(r.strategy_spec&&r.strategy_spec.kind==='classical_rules'){
   var rulesDownload=settingsBox.querySelector('a');rulesDownload.href=r.archive_url;rulesDownload.textContent='下载本次完整规则与账本';
  }
  $('[data-account]').insertAdjacentElement('afterend',settingsBox);
  if(r.strategy_spec&&r.strategy_spec.kind==='classical_rules'){
   var ruleDescription=document.createElement('details');ruleDescription.className='bt-report-audit';ruleDescription.dataset.ruleDescription='';
   ruleDescription.innerHTML='<summary>展开完整规则：基本面筛选、技术入场、退出与仓位</summary>'+(r.strategy_spec.description||[]).map(function(text){return '<p class="bt-report-note">'+esc(text)+'</p>';}).join('');
   settingsBox.insertAdjacentElement('beforebegin',ruleDescription);
  }
  var replayHost=document.createElement('section');settingsBox.insertAdjacentElement('afterend',replayHost);
  function plot(){var s=cfg.series,relative=$('[data-mode]').value==='relative',main=relative?[Object.assign({},s.excess_nav,{values:r.excess_nav||[]})]:['nav','bench_nav'].filter(function(k){return r[k]&&r[k].length;}).map(function(k){return Object.assign({},s[k],{values:r[k]});});
   var lanes=[{label:relative?'相对表现（大于1表示跑赢参考）':'净值（初始资金 = 1）',series:main},{label:'回撤（包含首日相对初始资金的亏损）',percent:true,zero:true,series:[Object.assign({},s.drawdown,{values:report.drawdown})]}];
   if(report.account_available){var map=new Map(report.account.map(function(x){return [x.date,x.exposure];}));lanes.push({label:'收盘股票仓位',percent:true,unit:'exposure',series:[Object.assign({},s.exposure,{values:dates.map(function(d){return map.get(d);})})]});}
   var events=Object.values((r.trades||[]).reduce(function(map,t){var key=t.date+t.side;if(!map[key])map[key]={date:t.date,side:t.side,count:0};map[key].count++;return map;},{})).map(function(e){return Object.assign(e,{label:(cfg.side_labels[e.side]||e.side)+' '+e.count+' 笔，点击查看当日复盘'});});
   chart($('[data-plot]'),dates,lanes,{events:events,onSelect:function(day){host.dispatchEvent(new CustomEvent('bt-select-date',{detail:{date:day}}));}});
  }
  $('[data-mode]').onchange=plot;plot();cleanups.push(function(){var p=$('[data-plot]');if(p._plotCleanup)p._plotCleanup();});
  var fillCodes=Array.from(new Set((r.trades||[]).map(function(t){return t.code;}))),fillSequence=0;
  $('[data-fill-code]').innerHTML=fillCodes.map(function(code){return '<option>'+esc(code)+'</option>';}).join('');
  $('[data-study-preset]').innerHTML='<option value="">自选指标</option>'+Object.keys(cfg.chart_presets||{}).map(function(k){return '<option value="'+esc(k)+'">'+esc(cfg.chart_presets[k].label)+'</option>';}).join('');
  $('[data-fill-indicators]').innerHTML=Object.keys(cfg.chart_indicators||{}).map(function(key){return '<label><input type="checkbox" value="'+esc(key)+'"'+(cfg.trade_chart.default_indicators.includes(key)?' checked':'')+'> '+esc(cfg.chart_indicators[key])+'</label>';}).join('');
  var indicatorHost=document.createElement('div');$('[data-fill-indicators]').insertAdjacentElement('afterend',indicatorHost);
  var fillParameterEditor=LW.indicatorEditor.mount(indicatorHost,cfg,cfg.trade_chart.default_indicators,r.params.indicator_params||{},loadFills);
  function loadFills(focusDate){
   var code=$('[data-fill-code]').value,seq=++fillSequence,chartHost=$('[data-fill-chart]');
   if(!code){$('[data-fill-status]').textContent='这次回测没有成交，因此没有可标注的买卖点。';return;}
   var indicators=Array.from($('[data-fill-indicators]').querySelectorAll('input:checked')).map(function(x){return x.value;});
   var timeframe=($('[data-fill-timeframe]')||{value:'day'}).value||'day';
   fillParameterEditor.setFactors(indicators);
   if(indicators.length>cfg.trade_chart.max_indicators){$('[data-fill-status]').textContent='最多显示 '+cfg.trade_chart.max_indicators+' 项技术指标，请取消几项。';return;}
   var query=new URLSearchParams({code:code,start:r.params.start,end:r.params.end,timeframe:timeframe,run:r.name,indicators:indicators.join(',')});
   query.set('indicator_params',JSON.stringify(fillParameterEditor.get()));
   $('[data-fill-link]').href='/research?'+query.toString()+'#stock-chart';
   $('[data-fill-status]').textContent='正在读取带买卖点的K线…';
   return LW.api.get('/api/research/chart?'+query.toString()).then(function(data){if(seq!==fillSequence)return;LW.chart.render(chartHost,data,{maxBars:cfg.trade_chart.max_bars});if(typeof focusDate==='string'&&chartHost._focusDay)chartHost._focusDay(focusDate);$('[data-fill-status]').textContent=data.trade_annotations?data.trade_annotations.title+' · '+data.trade_annotations.total_fills+' 笔成交':data.message;}).catch(function(e){if(seq===fillSequence)$('[data-fill-status]').textContent='K线未打开：'+e.message;});
  }
  $('[data-study-preset]').onchange=function(){var preset=cfg.chart_presets[this.value];if(!preset)return;$('[data-fill-indicators]').querySelectorAll('input').forEach(function(i){i.checked=preset.indicators.includes(i.value);});loadFills();};
  $('[data-fill-code]').onchange=$('[data-fill-indicators]').onchange=loadFills;
  var fillTimeframe=$('[data-fill-timeframe]');if(fillTimeframe)fillTimeframe.onchange=loadFills;
  loadFills();
  $('[data-trades]').onclick=function(e){var code=e.target.dataset.showCode;if(code){$('[data-fill-code]').value=code;loadFills(e.target.dataset.showDate);$('[data-fill-code]').scrollIntoView({behavior:'smooth',block:'center'});}};
  function replayStock(e){$('[data-fill-code]').value=e.detail.code;loadFills(e.detail.date);$('[data-fill-code]').scrollIntoView({behavior:'smooth',block:'center'});}
  host.addEventListener('bt-replay-stock',replayStock);cleanups.push(function(){host.removeEventListener('bt-replay-stock',replayStock);});
  cleanups.push(function(){fillSequence++;var ch=$('[data-fill-chart]');if(ch._chartCleanup)ch._chartCleanup();});
  var dd=report.max_drawdown;$('[data-dd]').textContent=dd.trough_date?'最大回撤 '+pct(dd.value)+' · 高点 '+(dd.peak_date||'初始资金')+' · 最低点 '+dd.trough_date+' · '+(dd.recovery_date?'回到此前高点 '+dd.recovery_date:'截至期末尚未回到此前高点'):'本次没有出现回撤。';
  $('[data-account]').textContent=report.account_available?'仓位来自逐日收盘账本；股票市值 ÷ 总资产，现金与股票合计等于总资产。':'这份历史记录没有逐日现金和持仓快照，无法展示仓位曲线。新运行的回测会保存这些数据。';
  var monthNumbers=Array.from({length:12},function(_,i){return String(i+1).padStart(2,'0');});
  $('[data-months]').innerHTML=table(['年份'].concat(monthNumbers.map(function(m){return Number(m)+'月';})).concat(['全年/区间']),report.months.map(function(y){return [esc(y.year)].concat(monthNumbers.map(function(m){var v=y.months[m];return v==null?'—':'<span class="'+(v>=0?'bt-return-up':'bt-return-down')+'">'+pct(v)+'</span>'; })).concat([pct(y.total)]); }),'bt-months');
  $('[data-months]').querySelectorAll('tbody tr').forEach(function(tr,i){monthNumbers.forEach(function(m,j){var v=report.months[i].months[m];if(number(v)){var alpha=Math.min(.6,.1+Math.abs(v)*2);tr.children[j+1].style.background=v>=0?'rgba(206,84,84,'+alpha+')':'rgba(43,157,124,'+alpha+')';tr.children[j+1].title=report.months[i].year+'-'+m+' 复合收益 '+pct(v);}});});
  $('[data-years]').innerHTML=table(['年份','复合收益'],report.years.map(function(y){return [esc(y.year),pct(y.ret)];}));
  var maxCount=Math.max(1,...report.histogram.map(function(h){return h.count;}));$('[data-hist]').innerHTML='<div class="bt-report-hist">'+report.histogram.map(function(h){return '<div style="height:'+Math.max(1,145*h.count/maxCount)+'px" title="'+pct(h.low)+' 至 '+pct(h.high)+'：'+h.count+' 天"><span>'+h.count+'</span></div>';}).join('')+'</div><p class="bt-report-note">横轴：日收益从低到高；纵轴：出现天数。'+(report.histogram.length?'区间 '+pct(report.histogram[0].low)+' 至 '+pct(report.histogram[report.histogram.length-1].high):'没有样本')+'</p>';
  var maxCost=Math.max(1,...report.costs.map(function(c){return c.value;}));$('[data-costs]').innerHTML='<div class="bt-report-bars">'+report.costs.map(function(c){return '<div class="bt-report-bar"><span>'+esc(c.label)+'</span><i style="width:'+100*c.value/maxCost+'%"></i><span>'+money(c.value)+' 元</span></div>';}).join('')+'</div><p class="bt-report-note">现金扣费 '+money(report.cash_fees)+' 元（佣金与印花税）。滑点已体现在成交价和净值中，上方金额用于解释其影响，不能再次从净值扣除。费用合计估计 '+money(r.costs.total)+' 元。</p>';
  $('[data-reasons]').innerHTML=report.rejections.length?table(['原因','次数'],report.rejections.map(function(x){return [esc(x.label),x.count];})):'<p class="bt-report-note">本次账本没有记录未成交请求。不代表已模拟所有真实市场成交限制。</p>';
  var page=0,size=Math.max(1,Number(cfg.page_size)||25),trades=r.trades||[];
  function drawTrades(){var q=$('[data-trade-q]').value.trim().toLowerCase(),side=$('[data-trade-side]').value,filtered=trades.filter(function(t){return (!q||t.code.toLowerCase().includes(q))&&(!side||t.side===side);}),pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.min(page,pages-1);$('[data-trades]').innerHTML=table(Object.values(columns),filtered.slice(page*size,(page+1)*size).map(function(t){return Object.keys(columns).map(function(k){if(k==='code')return '<button data-show-code="'+esc(t.code)+'" data-show-date="'+esc(t.date)+'">'+esc(t.code)+' 买卖点</button>';if(k==='side')return esc(cfg.side_labels[t[k]]||t[k]);if(k==='trigger')return esc((cfg.trigger_labels||{})[t[k]]||t[k]||'未记录');return number(t[k])?money(t[k]):esc(t[k]);});}),'bt-trade-table');$('[data-page-info]').textContent='第 '+(page+1)+' / '+pages+' 页 · 匹配 '+filtered.length+' / '+trades.length+' 笔';$('[data-page="prev"]').disabled=page===0;$('[data-page="next"]').disabled=page===pages-1;}
  $('[data-trade-q]').oninput=$('[data-trade-side]').onchange=function(){page=0;drawTrades();};$('[data-page="prev"]').onclick=function(){page--;drawTrades();};$('[data-page="next"]').onclick=function(){page++;drawTrades();};drawTrades();
  var rejectColumns={date:'日期',code:'代码',side:'方向',reason:'未成交原因'};
  var rejectionRows=r.rejections||[],rejectPage=0;
  function drawRejected(){var pages=Math.max(1,Math.ceil(rejectionRows.length/size));$('[data-rejected]').innerHTML=table(Object.values(rejectColumns),rejectionRows.slice(rejectPage*size,(rejectPage+1)*size).map(function(t){return [esc(t.date),esc(t.code),esc(cfg.side_labels[t.side]||t.side),esc(cfg.rejection_reasons[t.reason]||t.reason)];}),'bt-trade-table')+'<div class="bt-report-tools"><button data-reject-prev>上一页</button><span>第 '+(rejectPage+1)+' / '+pages+' 页</span><button data-reject-next>下一页</button></div>';$('[data-reject-prev]').disabled=rejectPage===0;$('[data-reject-next]').disabled=rejectPage===pages-1;$('[data-reject-prev]').onclick=function(){rejectPage--;drawRejected();};$('[data-reject-next]').onclick=function(){rejectPage++;drawRejected();};}drawRejected();
  var accountExportMap=new Map(report.account.map(function(x){return [x.date,x];}));host.querySelectorAll('[data-export]').forEach(function(b){b.onclick=function(){var kind=b.dataset.export;if(kind==='trades')csvDownload(r.name+'_trades',columns,trades);if(kind==='rejections')csvDownload(r.name+'_rejections',rejectColumns,rejectionRows);if(kind==='nav')csvDownload(r.name+'_nav',{date:'日期',nav:'策略净值',bench_nav:cfg.series.bench_nav.label,drawdown:'回撤',cash:'现金',market_value:'股票市值',exposure:'股票仓位'},dates.map(function(d,i){var a=accountExportMap.get(d)||{};return {date:d,nav:r.nav[i],bench_nav:(r.bench_nav||[])[i],drawdown:report.drawdown[i],cash:a.cash,market_value:a.market_value,exposure:a.exposure};}));};});
  if(r.strategy_spec&&r.strategy_spec.kind==='classical_rules'&&LW.ruleReplay)cleanups.push(LW.ruleReplay.mount(replayHost,r));
  else if(LW.tradeReplay)cleanups.push(LW.tradeReplay.mount(replayHost,r));
  host._reportCleanup=function(){cleanups.forEach(function(f){f();});};
 }
 function compare(host,runs){
  var dates=Array.from(new Set(runs.flatMap(function(r){return r.dates;}))).sort(),series=runs.map(function(r,i){var cfg=r.report.settings.series,keys=Object.keys(cfg),style=cfg[keys[i%keys.length]],map=new Map(r.dates.map(function(d,j){return [d,r.nav[j]];}));return {label:r.title+' · '+(r.generated_at||r.name)+' · '+r.params.start+' 至 '+r.params.end,color:style.color,values:dates.map(function(d){return map.get(d);})};});
  chart(host,dates,[{label:'按真实日期对齐，各次回测从初始资金1起算',series:series}]);
 }
 LW.backtestReport={render:render,chart:chart,compare:compare};
})();
