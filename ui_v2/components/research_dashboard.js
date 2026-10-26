/* API-backed workbench observations; opening this page never starts network collection. */
(function(){
 'use strict';
 var host=document.getElementById('workbench-market');if(!host)return;
 var days=null,sequence=0,timer=null,cleanups=[],contextHorizon=null,contextMetric='pressure',contextExpanded=false;
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 var num=function(n,dec){return typeof n==='number'&&isFinite(n)?n.toFixed(dec==null?1:dec):'待更新';};
 var pct=function(n){return typeof n==='number'?num(n*100,2)+'%':'缺数据';};
 function lineChart(box,series,label){
  series=series.filter(function(s){return s.values&&s.values.length;});
  if(!series.length){box.textContent='尚无足够历史数据，请等待更新或查看下方数据源状态。';return;}
  var enabled={},tools=document.createElement('div'),surface=document.createElement('div'),info=document.createElement('div');
  tools.className='market-tools';surface.className='market-line-surface';info.className='market-line-info';
  tools.innerHTML=series.map(function(s){enabled[s.id]=true;return '<label style="color:'+esc(s.color)+'"><input type="checkbox" data-series="'+esc(s.id)+'" checked> '+esc(s.name)+'</label>';}).join('')+'<button type="button">还原大小</button>';
  box.appendChild(tools);box.appendChild(surface);box.appendChild(info);
  var sizeKey='research.dashboard.size.'+box.id;
  try{var saved=JSON.parse(localStorage.getItem(sizeKey)||'null');if(saved&&saved.height)surface.style.height=Math.max(230,Math.min(1000,saved.height))+'px';}catch(e){}
  function render(){
   var active=series.filter(function(s){return enabled[s.id];}),points=active.flatMap(function(s){return s.values;});
   if(!points.length){surface.innerHTML='<p class="market-caption">勾选上方至少一条曲线。</p>';return;}
   var w=Math.max(400,surface.clientWidth),h=Math.max(230,surface.clientHeight),left=52,right=w-18,top=18,bottom=h-28;
   var timestamps=points.map(function(p){return Date.parse(p.date+'T00:00:00Z');}),first=Math.min.apply(null,timestamps),last=Math.max.apply(null,timestamps);
   var low=Math.min.apply(null,points.map(function(p){return p.value;})),high=Math.max.apply(null,points.map(function(p){return p.value;})),span=high-low||1;
   low-=span*.06;high+=span*.06;
   function x(day){return left+(Date.parse(day+'T00:00:00Z')-first)/(last-first||1)*(right-left);}
   function y(value){return top+(high-value)/(high-low)*(bottom-top);}
   var svg='<svg viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+esc(label)+'">';
   for(var i=0;i<4;i++){var value=low+(high-low)*i/3;svg+='<line x1="'+left+'" x2="'+right+'" y1="'+y(value)+'" y2="'+y(value)+'" stroke="#2e3747"/><text x="4" y="'+(y(value)+3)+'" font-size="10" fill="#a5b2c7">'+num(value,1)+'</text>';}
   active.forEach(function(s){svg+='<polyline data-line="'+esc(s.id)+'" points="'+s.values.map(function(p){return x(p.date)+','+y(p.value);}).join(' ')+'" fill="none" stroke="'+esc(s.color)+'" stroke-width="1.8"/>';});
   var dates=Array.from(new Set(points.map(function(p){return p.date;}))).sort();
   svg+='<line class="market-crosshair" x1="0" x2="0" y1="'+top+'" y2="'+bottom+'" stroke="#aab3c4" stroke-dasharray="3 4" visibility="hidden"/><text x="'+left+'" y="'+(h-7)+'" fill="#a5b2c7" font-size="10">'+esc(dates[0])+'</text><text x="'+right+'" y="'+(h-7)+'" text-anchor="end" fill="#a5b2c7" font-size="10">'+esc(dates[dates.length-1])+'</text></svg>';
   surface.innerHTML=svg;
   info.textContent=active.map(function(s){var p=s.values[s.values.length-1];return s.name+'：'+num(p.value,2)+'（'+p.date+'）';}).join(' · ');
   surface.onpointermove=function(ev){
    var bounds=surface.getBoundingClientRect(),px=(ev.clientX-bounds.left)/bounds.width*w,target=first+Math.max(0,Math.min(1,(px-left)/(right-left)))*(last-first);
    var day=dates.reduce(function(best,d){return Math.abs(Date.parse(d)-target)<Math.abs(Date.parse(best)-target)?d:best;},dates[0]);
    var cross=surface.querySelector('.market-crosshair');if(cross){cross.setAttribute('x1',x(day));cross.setAttribute('x2',x(day));cross.setAttribute('visibility','visible');}
    info.textContent=day+' · '+active.map(function(s){var p=s.values.find(function(p){return p.date===day;});return s.name+' '+(p?num(p.value,2):'当日无数据');}).join(' · ');
   };
  }
  tools.onchange=function(e){if(e.target.dataset.series){enabled[e.target.dataset.series]=e.target.checked;render();}};
  tools.querySelector('button').onclick=function(){surface.style.height='';surface.style.width='';try{localStorage.removeItem(sizeKey);}catch(e){}render();};
  var resize=new ResizeObserver(function(){render();try{localStorage.setItem(sizeKey,JSON.stringify({height:surface.clientHeight}));}catch(e){}});resize.observe(surface);cleanups.push(function(){resize.disconnect();});render();
 }
 function kpi(title,value,note){return '<article class="market-kpi"><h3>'+esc(title)+'</h3><strong>'+esc(value)+'</strong><p>'+esc(note)+'</p></article>';}
 function historyPanel(data){
  var box=document.getElementById('market-score-context');if(!box)return;
  if(!data||!data.items){box.innerHTML='<h2>这些读数意味着什么？</h2><p class="market-caption">更新市场数据后，将按历史区间比较后续涨跌。</p>';return;}
  contextHorizon=data.horizons[contextHorizon]?contextHorizon:data.default_horizon;
  var horizon=data.horizons[contextHorizon],move=pct(horizon.large_move),items=data.items;
  var selected=items.find(function(x){return x.id===contextMetric;})||items[0];contextMetric=selected.id;
  function currentGroup(item){return item.current_bin==null?null:item.outcomes[contextHorizon].bins.find(function(b){return b.id===item.current_bin;});}
  function rate(s,key){if(!s||!s.n||s[key]==null)return '暂无';return pct(s[key]);}
  function rateCounts(s,key,count){return rate(s,key)+(s&&s.n?'（'+s[count]+'/'+s.n+'）':'');}
  var group=currentGroup(selected),result=selected.outcomes[contextHorizon],baseline=result.baseline;
  var summary=group?'当前'+num(selected.value)+selected.unit+'，属于“'+group.label+'”。这个区间有'+group.full.n+'次不重叠的观测；'+horizon.label+'大涨'+move+'及以上占'+rate(group.full,'big_up')+'，大跌'+move+'及以上占'+rate(group.full,'big_down')+'。同一市场所有区间的大跌基准为'+rate(baseline,'big_down')+'。'+group.verdict+'。':'当前数据或历史不足，暂时不能判断这个读数之后更容易涨还是跌。';
  function statsRow(label,s,tail,current,verdict){return '<tr'+(current?' class="market-current-bin"':'')+'><td>'+esc(label)+(current?' <b>← 当前</b>':'')+'</td><td>'+s.n+'次<br><small>'+s.days+'个信号日</small></td><td>'+pct(s.mean)+'<br><small>中位数 '+pct(s.median)+'</small></td><td>'+rateCounts(s,'big_up','up_count')+'</td><td>'+rateCounts(s,'big_down','down_count')+'</td><td>'+rate(s,'path_down')+'</td><td>'+tail.n+'次<br>涨 '+rate(tail,'big_up')+' / 跌 '+rate(tail,'big_down')+'</td><td>'+esc(verdict||'所有区间的对照')+'</td></tr>';}
  box.innerHTML='<h2>这些读数之后，历史上更容易涨还是跌？</h2><div class="market-tools"><label>观察多久 <select id="market-context-horizon">'+Object.keys(data.horizons).map(function(k){return '<option value="'+esc(k)+'"'+(k===contextHorizon?' selected':'')+'>'+esc(data.horizons[k].label)+'</option>';}).join('')+'</select></label><span>本表“大涨 / 大跌”：期末涨跌达到 '+move+'</span></div><div class="market-context-scroll"><table class="market-context-table"><thead><tr><th>指标（点击查看各区间）</th><th>当前读数 / 区间</th><th>对应市场</th><th>大涨比例</th><th>大跌比例</th><th>所有区间大跌基准</th><th>观察次数</th><th>历史提示</th></tr></thead><tbody>'+items.map(function(item){var g=currentGroup(item),r=item.outcomes[contextHorizon];return '<tr'+(item.id===selected.id?' class="market-selected-metric"':'')+'><td><button type="button" data-context-metric="'+esc(item.id)+'">'+esc(item.name)+'</button></td><td>'+num(item.value)+esc(item.unit)+'<br><small>'+esc(g?g.label:'尚无可用区间')+'</small></td><td>'+esc(item.target_name)+'</td><td>'+rate(g&&g.full,'big_up')+'</td><td>'+rate(g&&g.full,'big_down')+'</td><td>'+rate(r.baseline,'big_down')+'</td><td>'+(g?g.full.n:0)+'次</td><td>'+esc(g?g.verdict:'历史不足，暂不判断')+'</td></tr>';}).join('')+'</tbody></table></div>'+
   '<details class="market-context-detail"'+(contextExpanded?' open':'')+'><summary>'+esc(selected.name)+'：查看分区间统计与样本量</summary><p class="market-caption">'+esc(selected.description)+'</p><p id="market-context-reading" class="market-context-reading">'+esc(summary)+'</p><p class="market-caption">可用分数历史：'+selected.history_days+'个日期'+(selected.history_from?'，起于'+esc(selected.history_from):'')+'。本期收益已有结果的信号区间：'+esc(result.observed_from||'无')+' 至 '+esc(result.observed_to||'无')+'。最近'+num(data.validation_fraction*100,0)+'%日期从'+esc(selected.validation_from||'暂无')+'起单独检查。</p><div class="market-context-scroll"><table class="market-context-table"><thead><tr><th>读数区间</th><th>不重叠观测</th><th>期末平均涨跌</th><th>期末大涨</th><th>期末大跌</th><th>期间曾跌到门槛</th><th>最近一段复核</th><th>历史提示</th></tr></thead><tbody>'+statsRow('所有区间基准',baseline,result.recent_baseline,false)+result.bins.map(function(b){return statsRow(b.label,b.full,b.recent,b.current&&selected.value!=null,b.verdict);}).join('')+'</tbody></table></div><p class="market-caption">至少'+data.min_samples+'次观测且最近一段至少'+data.min_validation_samples+'次才给出倾向提示；不足时比例只供查看，不能据此判断好坏。'+esc(selected.data_note)+'</p></details>'+
   '<p class="market-caption">'+esc(data.method)+'</p><p class="market-caption">'+esc(data.caution)+'</p>';
  box.querySelector('.market-context-detail').ontoggle=function(){contextExpanded=this.open;};
  document.getElementById('market-context-horizon').onchange=function(){contextHorizon=this.value;historyPanel(data);};
  box.querySelectorAll('[data-context-metric]').forEach(function(button){button.onclick=function(){contextMetric=button.dataset.contextMetric;historyPanel(data);};});
 }
 function freshness(d,fear){
  // 一行汇总所有数据源是否过旧，不必展开底部表格逐项核对。
  var u=d.update||{},feeds=Object.keys(d.feeds||{}).map(function(k){return d.feeds[k];});
  var bad=feeds.filter(function(f){return f.state==='error'||f.stale||!f.date;});
  var stale=fear.filter(function(f){return f.stale;}).length+(d.sectors||[]).filter(function(s){return s.stale;}).length;
  if(!feeds.length)return '<div class="market-fresh empty" role="status">还没有市场数据。点击「更新市场数据」获取；未更新前下方读数显示为“待更新”。</div>';
  if(bad.length||stale)return '<div class="market-fresh warn" role="status"><span>'+bad.length+' / '+feeds.length+' 项数据过旧或更新失败'+(bad.length?'：'+bad.slice(0,4).map(function(f){return esc(f.name);}).join('、')+(bad.length>4?' 等':''):'')+(stale?' · '+stale+' 个读数已过期，不参与历史判断':'')+'</span><button type="button" class="lf-btn" data-show-sources>查看数据来源</button></div>';
  return '<div class="market-fresh" role="status"><span>'+feeds.length+' 项数据都在有效期内'+(u.finished_at?' · 最近更新 '+esc(String(u.finished_at).replace('T',' ').slice(0,16)):'')+'</span></div>';
 }
 var collapsed=true;try{collapsed=localStorage.getItem('research.dashboard.collapsed')!=='0';}catch(e){}
 var lastSignature=null;
 function render(d){
  // 重画前记住用户展开过的说明块，重画后恢复，避免轮询把它们关上。
  var openDetails=Array.from(host.querySelectorAll('details[id]')).filter(function(x){return x.open;}).map(function(x){return x.id;});
  cleanups.forEach(function(fn){fn();});cleanups=[];
  var u=d.update||{},fear=d.fear||[],pressure=d.pressure||{},div=d.dividend||{},compare=d.comparison||{};
  var overview=kpi(pressure.label||'A股市场压力',num(pressure.score)+' / 100',(pressure.date||'等待数据')+' · 越高压力越大 · 覆盖 '+(pressure.coverage||0)+' / '+(pressure.total||0)+' 个行业');
  // 恐慌指标只在卡片给出读数和历史位置；曲线图负责看走势，不再重复列数值说明。
  fear.forEach(function(f){overview+=kpi(f.name,num(f.value)+'%',(f.date||'等待数据')+(f.stale?' · 数据过旧':'')+' · 历史位置 '+num(f.percentile)+' / 100');});
  overview+=kpi(div.label||'红利低波性价比',num(div.score)+' / 100',(div.date||'等待数据')+' · 越高越有吸引力');
  var heat='<section class="v2-card"><h2>A股板块温度</h2><p class="market-caption">'+esc(d.temperature_description||'等待板块历史数据。')+'</p><div class="market-heatmap">'+(d.sectors||[]).map(function(s){var t=s.temperature,color=typeof t==='number'?'hsl('+(155-t*1.55)+',35%,22%)':'#292d36';return '<button type="button" class="market-sector" style="background:'+color+'" data-sector="'+esc(s.id)+'"><b>'+esc(s.name)+'</b><strong>'+num(t)+'<small style="font-size:11px"> / 100</small></strong><span>20日涨跌 '+pct(s.return_20)+'</span><span>'+esc(s.date||'无数据')+(s.stale?' · 过旧':'')+'</span></button>';}).join('')+'</div><p id="sector-detail" class="market-caption">点击板块查看回撤、波动率和分项得分。冷色偏冷、暖色偏热；温度不等于估值。</p></section>';
  host.classList.toggle('collapsed',collapsed);
  host.innerHTML='<div class="market-heading"><h2>市场观察</h2><span id="market-refresh-status" class="market-caption" role="status">'+esc(u.message||'等待更新')+(u.state==='running'?' '+u.completed+' / '+u.total:'')+'</span><button class="lf-btn" id="market-more-toggle" aria-expanded="'+(!collapsed)+'">'+(collapsed?'展开详细分析':'收起详细分析')+'</button><button class="lf-btn" id="market-refresh" '+(u.state==='running'?'disabled':'')+'>更新市场数据</button></div>'+freshness(d,fear)+
   '<div class="market-overview">'+overview+'</div>'+heat+
   '<div class="market-more"><section class="v2-card market-score-context" id="market-score-context"></section>'+
   '<div class="market-panels"><section class="v2-card"><h2>沪深300、纳斯达克、标普500</h2><div class="market-tools"><label>比较区间 <select id="market-compare-period">'+Object.keys(d.periods||{}).map(function(k){return '<option value="'+esc(k)+'"'+(Number(k)===d.days?' selected':'')+'>'+esc(d.periods[k])+'</option>';}).join('')+'</select></label><span>'+esc(compare.start||'')+' 至 '+esc(compare.end||'')+' · '+(compare.points||0)+' 个共同交易日</span></div><div id="workbench-comparison"></div><p class="market-caption">'+esc(compare.description||compare.message||'')+'</p></section>'+
   '<section class="v2-card"><h2>期权恐慌指标走势</h2><div id="workbench-fear-chart"></div><details class="market-caption" id="market-fear-notes"><summary>指标说明</summary>'+fear.map(function(f){return '<p>'+esc(f.description)+'</p>';}).join('')+'</details></section></div>'+
   '<section class="v2-card"><h2>'+esc(div.label||'红利低波性价比分数')+'</h2><p class="market-caption">'+esc(div.description||'')+' 股息率 '+num(div.dividend_yield,2)+'% · 10年国债 '+num(div.bond_yield,2)+'%</p><div class="market-score-parts">'+Object.keys(div.components||{}).map(function(k){var p=div.components[k],score=p.score;return '<div class="market-score-part"><b>'+esc(p.label)+'：'+num(score)+' / 100</b><span style="float:right">权重 '+num(div.weights[k]*100,0)+'%</span><p>原值 '+(p.unit==='ratio'?pct(p.value):num(p.value,2)+' '+esc(p.unit))+'</p><div class="market-score-bar"><i style="width:'+(typeof score==='number'?Math.max(0,Math.min(100,score)):0)+'%"></i></div><p>'+esc(p.formula)+'</p></div>';}).join('')+'</div><p class="market-caption">'+esc(div.message||'')+' '+Object.keys(div.input_dates||{}).map(function(k){return esc({dividend:'股息率',bond:'国债',price:'价格'}[k]||k)+'日期 '+esc(div.input_dates[k]);}).join(' · ')+'</p><p class="market-caption">'+esc(pressure.description||'')+' 红利低波历史股息率可在 <a href="/data#market-history-import">股票与数据</a> 导入。</p></section>'+
   '<details class="market-data-details" id="market-sources"><summary>数据来源与更新时间</summary><div style="overflow:auto"><table class="market-source-table"><thead><tr><th>数据</th><th>数据日期</th><th>来源</th><th>更新时间 / 状态</th></tr></thead><tbody>'+Object.keys(d.feeds||{}).map(function(k){var f=d.feeds[k];return '<tr><td>'+esc(f.name)+'</td><td>'+esc(f.date||'尚无')+(f.stale?' <span class="market-warning">过旧</span>':'')+'</td><td><a href="'+esc(f.url)+'" target="_blank" rel="noopener">'+esc(f.source)+'</a></td><td>'+esc(f.updated_at||'未成功更新')+(f.error?'<br><span class="market-warning">'+esc(f.error)+'</span>':'')+'</td></tr>';}).join('')+'</tbody></table></div></details></div>';
  openDetails.forEach(function(id){var el=document.getElementById(id);if(el)el.open=true;});
  function drawMore(){
   if(host._moreDrawn)return;host._moreDrawn=true;
   historyPanel(d.interpretation);
   lineChart(document.getElementById('workbench-comparison'),compare.series||[],'三指数起点100对比图');
   lineChart(document.getElementById('workbench-fear-chart'),fear.map(function(f){return {id:f.id,name:f.name,color:f.color,values:f.values||[]};}),'QVIX与VXN隐含波动率');
   document.getElementById('market-compare-period').onchange=function(){days=Number(this.value);load();};
  }
  host._moreDrawn=false;if(!collapsed)drawMore();   // 折叠时不渲染图表，展开时再画（图表需要真实宽度）
  document.getElementById('market-more-toggle').onclick=function(){collapsed=!collapsed;try{localStorage.setItem('research.dashboard.collapsed',collapsed?'1':'0');}catch(e){}host.classList.toggle('collapsed',collapsed);this.textContent=collapsed?'展开详细分析':'收起详细分析';this.setAttribute('aria-expanded',String(!collapsed));if(!collapsed)drawMore();};
  var sourcesBtn=host.querySelector('[data-show-sources]');if(sourcesBtn)sourcesBtn.onclick=function(){if(collapsed)document.getElementById('market-more-toggle').click();var s=document.getElementById('market-sources');s.open=true;s.scrollIntoView({behavior:'smooth',block:'center'});};
  document.getElementById('market-refresh').onclick=function(){this.disabled=true;LW.api.post('/api/studio/dashboard/refresh',{}).then(load).catch(function(e){document.getElementById('market-refresh-status').textContent='更新未启动：'+e.message;document.getElementById('market-refresh').disabled=false;});};
  host.querySelectorAll('[data-sector]').forEach(function(button){button.onclick=function(){var s=d.sectors.find(function(s){return s.id===button.dataset.sector;});document.getElementById('sector-detail').textContent=s.name+' · '+s.date+' · 近一年高点回撤 '+pct(s.drawdown)+' · 20日年化波动 '+pct(s.volatility)+' · 涨幅位置 '+num(s.components&&s.components.momentum)+' · 回撤位置 '+num(s.components&&s.components.drawdown)+' · 低波动 '+num(s.components&&s.components.volatility);};});
 }
 function load(){
  var seq=++sequence;if(timer)clearTimeout(timer);
  return LW.api.get('/api/studio/dashboard'+(days?'?days='+days:''),{bypass:true}).then(function(d){if(seq!==sequence)return;days=d.days;
   // 数据没变（更新进行中只有进度在变）时只改状态文字，不重画整块，点击和展开状态不受影响。
   var signature=JSON.stringify(Object.assign({},d,{update:null})),u=d.update||{},status=document.getElementById('market-refresh-status'),button=document.getElementById('market-refresh');
   if(signature===lastSignature&&status&&button){status.textContent=(u.message||'等待更新')+(u.state==='running'?' '+u.completed+' / '+u.total:'');button.disabled=u.state==='running';}
   else{lastSignature=signature;render(d);}
   timer=setTimeout(load,(d.update.state==='running'?d.poll_seconds:60)*1000);}).catch(function(e){if(seq!==sequence)return;var status=document.getElementById('market-refresh-status');if(status)status.textContent='读取失败：'+e.message;else host.textContent='市场观察暂未加载：'+e.message;timer=setTimeout(load,10000);});
 }
 load();
})();
