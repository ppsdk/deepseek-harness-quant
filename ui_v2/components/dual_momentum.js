/* 双层动量：横截面选股 + 个股时间序列择时。四组对照、增量检验、参数稳健性与 AI 复核，数据来自 /api/studio/dual。 */
(function(){
 'use strict';var S=LW.studio,esc=S.esc;
 var GRADE={'优秀':'dm-v5','良好':'dm-v4','观察':'dm-v3','勉强':'dm-v2','无效':'dm-v1'};
 var PERIOD={train:'训练期',test:'样本外',all:'全区间'};
 function tag(v){return v?'<span class="dm-tag '+(GRADE[v]||'dm-v1')+'">'+esc(v)+'</span>':'';}
 function pct(v,d){return typeof v==='number'?(v*100).toFixed(d==null?2:d)+'%':'—';}
 function num(v,d){return typeof v==='number'?v.toFixed(d==null?2:d):'—';}
 function money(v){return typeof v==='number'?v.toLocaleString('zh-CN',{maximumFractionDigits:0}):'—';}
 function signed(v,f){return typeof v==='number'?(v>0?'+':'')+f(v):'—';}
 function tone(v,s){return typeof v==='number'?'<span class="'+(v>0?'bt-return-up':v<0?'bt-return-down':'')+'">'+s+'</span>':s;}
 function text(s){return esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/\n/g,'<br>');}
 function table(head,rows){return '<div class="studio-scroll"><table class="studio-table"><thead><tr>'+head.map(function(h){return '<th>'+h+'</th>';}).join('')+'</tr></thead><tbody>'+rows.map(function(r){var cells=r.cells||r;return '<tr'+(r.cls?' class="'+r.cls+'"':'')+'>'+cells.map(function(v){return '<td>'+(v==null?'—':v)+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div>';}
 function labels(obj,key){var out={};Object.keys(obj||{}).forEach(function(k){out[k]=key?obj[k][key]:obj[k];});return out;}

 function mount(host){
  host=typeof host==='string'?S.$(host):host;if(!host)return;
  var cfg=null,rec=null,runs=[],timer=null,seq=0;
  host.innerHTML='<section class="v2-card" id="dual-momentum"><h2>双层动量：横截面选股 + 个股择时</h2>'+
   '<p class="studio-note">每隔固定交易日按选股因子选出前 N 名作为候选名单；名单内的股票每天再看自己的择时因子，只有“在名单内且择时成立”才持有，每只目标 1/N，其余留现金。择时转弱卖出后，同一名单期内重新转强可以买回；落选的股票要等下次更新名单。收盘出信号、次日开盘成交，停牌、一字板和涨跌停不成交，费用按回测成本模型扣除。每次同时跑 A 基准、B 纯选股、C 纯择时、D 双层四组对照。</p>'+
   '<div id="dm-form" class="studio-controls"></div><div class="studio-actions"><button class="studio-button" id="dm-run">运行四组对照</button></div><p id="dm-status" class="studio-note" role="status"></p>'+
   '<div class="studio-controls"><label>历史结果<select id="dm-history"></select></label><label>评级<select id="dm-grade"></select></label><button class="studio-button" id="dm-open">打开</button></div>'+
   '<div id="dm-result"></div>'+
   '<h3>参数稳健性</h3><p class="studio-note">在当前结果的股票池和区间上，把同一个动量窗口同时用于选股和择时，遍历窗口、调仓间隔和阈值。只按训练期夏普挑参数，再看它在样本外的排名和邻近参数的表现；邻近参数也好，结论才算稳。</p>'+
   '<div class="studio-controls"><label>动量窗口（交易日）<input id="dm-g-lb"></label><label>调仓间隔（交易日）<input id="dm-g-rb"></label><label>择时阈值（%）<input id="dm-g-th"></label><button class="studio-button" id="dm-grid-run" disabled>运行参数检验</button></div><p id="dm-grid-status" class="studio-note" role="status"></p><div id="dm-grid"></div>'+
   '<h3>AI 复核</h3><div class="studio-actions"><button class="studio-button" id="dm-ai-run" disabled>发给 HARNESS 复核</button></div><p id="dm-ai-status" class="studio-note" role="status"></p><div id="dm-ai"></div>'+
   '<div id="dm-notes"></div></section>';

  function factor(k){return cfg&&cfg.factor_choices[k]||k;}
  function form(){
   var st=cfg.settings,d=st.defaults,specs={universe:{label:'股票池',type:'select',options:labels(st.universes,'label')}};
   function num(k){var f=st.fields[k]||cfg.costs[k];if(!f)return;specs[k]={label:f.label,type:f.type||'number',min:f.min,max:f.max,step:f.integer?1:(f.step||'any'),default:f.default};}
   ['start','end'].forEach(num);
   specs.cs_factor={label:'选股因子',type:'select',options:cfg.factor_choices};
   specs.cs_sign={label:'选股方向',type:'select',options:st.signs||{'1':'因子值高的优先','-1':'因子值低的优先'}};
   ['cs_n','topn','rebalance_days'].forEach(num);
   specs.ts_factor={label:'择时因子',type:'select',options:cfg.factor_choices};
   specs.ts_direction={label:'择时条件',type:'select',options:st.directions};
   ['ts_n','ts_threshold','initial_capital','rebalance_threshold_pct','horizon','cache_stocks'].concat(Object.keys(cfg.costs)).forEach(num);
   S.fields('dm-form',specs,d);
   var g=st.grid||{};S.$('dm-g-lb').value=(g.lookbacks||[]).join(',');S.$('dm-g-rb').value=(g.rebalances||[]).join(',');S.$('dm-g-th').value=(g.thresholds||[]).join(',');
   S.$('dm-g-th').parentNode.title='最多 '+(g.max_runs||'—')+' 组组合';
   drawNotes(null);
  }
  function drawHistory(selected){
   var grade=S.$('dm-grade'),cur=grade.value,present=Object.keys(GRADE).filter(function(k){return runs.some(function(h){return h.verdict===k;});});
   grade.innerHTML='<option value="">全部评级</option>'+present.map(function(k){return '<option'+(k===cur?' selected':'')+'>'+esc(k)+'</option>';}).join('');
   var shown=runs.filter(function(h){return !grade.value||h.verdict===grade.value;});
   S.$('dm-history').innerHTML=shown.length?shown.map(function(h){return '<option value="'+esc(h.id)+'">'+esc(String(h.generated_at||'').replace('T',' ').slice(0,16))+' · '+esc(h.universe)+' · '+esc(h.title)+' · '+esc(h.verdict)+' · 夏普 '+num(h.sharpe)+(h.has_grid?' · 含参数检验':'')+'</option>';}).join(''):'<option value="">'+(runs.length?'没有这个评级的结果':'还没有结果')+'</option>';
   if(selected)S.$('dm-history').value=selected;
  }
  function overview(selected){return LW.api.get('/api/studio/dual',{bypass:true}).then(function(o){if(!cfg){cfg=o;form();}runs=o.history||[];drawHistory(selected);return o;});}
  function open(id){if(!id)return;var r=++seq;S.$('dm-status').textContent='正在读取结果…';return LW.api.get('/api/studio/dual/detail?id='+encodeURIComponent(id),{bypass:true}).then(function(d){if(r!==seq)return;S.$('dm-status').textContent='';show(d.record);}).catch(function(e){if(r===seq)S.$('dm-status').textContent=e.message;});}
  function completed(r){show(r.record);overview(r.record.id);}

  function show(r){
   rec=r;S.$('dm-grid-run').disabled=false;S.$('dm-ai-run').disabled=false;
   try{var q=new URLSearchParams(location.search);q.set('dual',r.id);window.history.replaceState(null,'',location.pathname+'?'+q+location.hash);}catch(e){}
   var p=r.params,u=r.universe||{},V=r.variants,keys=Object.keys(V),c=r.comparison||{},sc=c.scaled_cross_section||{},inc=r.incremental||{},L=r.latest||{};
   var html='<h3>结果概况</h3><p class="studio-note">'+esc(u.label)+(u.as_of?'（成分股截至 '+esc(u.as_of)+'）':'')+' · 本机有日线 '+(u.used==null?'—':u.used)+' / '+(u.listed==null?'—':u.listed)+' 只 · '+esc(r.dates[0])+' 至 '+esc(r.dates[r.dates.length-1])+' · 训练期与样本外分界 '+esc(r.split_date)+'<br>选股：'+esc(factor(p.cs_factor))+'（'+p.cs_n+' 日）'+(Number(p.cs_sign)===-1?'低值':'高值')+'优先，前 '+p.topn+' 名，每 '+p.rebalance_days+' 个交易日更新 · 择时：'+esc(factor(p.ts_factor))+'（'+p.ts_n+' 日）'+(p.ts_direction==='above'?'高于':'低于')+' '+p.ts_threshold+(r.elapsed_s!=null?' · 用时 '+num(r.elapsed_s,1)+' 秒':'')+'</p>'+
    (r.warnings&&r.warnings.length?'<p class="studio-note">注意：'+r.warnings.map(esc).join('；')+'</p>':'');
   html+=table(['方案','评级','年化','夏普','最大回撤','样本外年化','样本外夏普','平均股票仓位','平均持股','年换手（倍）','交易笔数','总成本（元）'],keys.map(function(k){var v=V[k],m=v.metrics,t=v.test||{};return ['<span class="dm-swatch" style="background:'+esc(v.color)+'"></span>'+esc(v.label),tag(v.verdict),tone(m.annual_return,pct(m.annual_return)),num(m.sharpe),pct(m.max_drawdown),tone(t.annual_return,pct(t.annual_return)),num(t.sharpe),pct(m.avg_exposure,0),num(m.avg_holdings,1),num(m.turnover,1),m.trades,money(m.costs)];}))+
    '<p class="studio-note">'+keys.map(function(k){return '<b>'+esc(V[k].short)+'</b>：'+esc(V[k].note);}).join('<br>')+'</p>';
   if(V.dual&&V.cross_section)html+='<h3>择时带来了什么（双层 − 纯选股）</h3><div class="studio-kpis">'+[['年化差',signed(c.delta_return,pct)],['夏普差（全区间）',signed(c.delta_sharpe,num)],['夏普差（样本外）',signed(c.delta_sharpe_test,num)],['最大回撤改善',signed(c.delta_drawdown,pct)]].map(function(x){return '<span>'+x[0]+'<b>'+x[1]+'</b></span>';}).join('')+'</div>'+
    '<p class="studio-note">把纯选股按双层的平均仓位 '+pct(sc.exposure,0)+' 缩放（其余放现金）后：年化 '+pct(sc.annual_return)+'、夏普 '+num(sc.sharpe)+'、最大回撤 '+pct(sc.max_drawdown)+'。双层年化 '+pct(V.dual.metrics.annual_return)+'、最大回撤 '+pct(V.dual.metrics.max_drawdown)+'；只有明显好于这个缩放版本，提升才不只是来自少持股、多留现金。</p>';
   html+='<div id="dm-chart"></div>';
   var inc_rows=[];[['candidates','名单内：择时成立 − 不成立'],['universe','全股票池：择时成立 − 不成立']].forEach(function(g){Object.keys(PERIOD).forEach(function(per){var x=(inc[g[0]]||{})[per]||{};inc_rows.push([esc(g[1]),PERIOD[per],tone(x.mean,signed(x.mean,pct)),num(x.t),pct(x.hit,0),pct(x.on),pct(x.off),num(x.on_count,1)+' / '+num(x.off_count,1),x.n]);});});
   Object.keys(PERIOD).forEach(function(per){var x=(inc.ic||{})[per]||{};inc_rows.push(['横截面因子排序相关（IC）',PERIOD[per],num(x.mean,3),num(x.t),pct(x.hit,0),'—','—','—',x.n]);});
   html+='<h3>增量检验：未来 '+esc(inc.horizon)+' 个交易日收益</h3><p class="studio-note">比较 E[R | 在名单内, 择时成立] 与 E[R | 在名单内, 择时不成立]，收益按次日开盘到 '+esc(inc.horizon)+' 日后开盘计算，样本每 '+esc(inc.horizon)+' 日取一次以避免重叠。IC 行回答“横截面因子本身能否预测收益”。</p>'+
    table(['检验','区间','平均差值 / IC','t 值','为正的比例','成立组平均','不成立组平均','平均只数（成立/不成立）','独立样本数'],inc_rows)+(inc.conclusion&&inc.conclusion.length?'<ul class="studio-note">'+inc.conclusion.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul>':'');
   html+='<h3>下一交易日信号</h3><p class="studio-note">'+esc(L.message||'')+(L.signal_date?' 信号按 '+esc(L.signal_date)+' 收盘计算，次日开盘执行。':'')+'</p>'+
    table(['名次','股票','选股因子值','择时因子值','择时','模拟持仓','次日动作'],(L.rows||[]).map(function(x){return [x.rank,'<a href="/research?code='+encodeURIComponent(x.code)+'#stock-chart">'+esc(x.code)+'</a> '+esc(x.name||''),num(x.cs),num(x.ts),x.ts_ok?'成立':'不成立',x.held?'持有':'空仓',esc(x.action)];}));
   var reasons=Array.from(new Set((r.trades||[]).map(function(t){return t.reason;})));
   html+='<h3>双层策略成交记录</h3><div class="studio-controls"><label>股票代码<input id="dm-t-code" placeholder="例如 600519"></label><label>原因<select id="dm-t-reason"><option value="">全部原因</option>'+reasons.map(function(x){return '<option>'+esc(x)+'</option>';}).join('')+'</select></label></div><p id="dm-t-note" class="studio-note"></p><div id="dm-trades"></div>';
   S.$('dm-result').innerHTML=html;
   if(LW.backtestReport)LW.backtestReport.chart(S.$('dm-chart'),r.dates,[
    {label:'净值',series:keys.map(function(k){return {label:V[k].short,color:V[k].color,values:V[k].nav};})},
    {label:'回撤',percent:true,zero:true,series:keys.map(function(k){return {label:V[k].short+' 回撤',color:V[k].color,values:V[k].drawdown};})},
    {label:'收盘股票仓位',percent:true,unit:'exposure',series:keys.map(function(k){return {label:V[k].short+' 仓位',color:V[k].color,values:V[k].exposure};})}]);
   function drawTrades(){var code=S.$('dm-t-code').value.trim().toUpperCase(),reason=S.$('dm-t-reason').value,list=(r.trades||[]).filter(function(t){return (!code||t.code.indexOf(code)>=0)&&(!reason||t.reason===reason);}),shown=list.slice(-200).reverse();
    S.$('dm-t-note').textContent='共 '+(r.trade_count==null?(r.trades||[]).length:r.trade_count)+' 笔，筛选后 '+list.length+' 笔，显示最近 '+shown.length+' 笔。';
    S.$('dm-trades').innerHTML=table(['成交日','信号日','股票','方向','原因','开盘价','成交价','金额（元）'],shown.map(function(t){return [esc(t.date),esc(t.signal_date),esc(t.code),t.side==='buy'?'买入':'卖出',esc(t.reason),num(t.open),num(t.fill_price,3),money(t.amount)];}));}
   S.$('dm-t-code').oninput=S.$('dm-t-reason').onchange=drawTrades;drawTrades();
   drawGrid(r.grid);drawAI(r.ai);drawNotes(r.notes);
  }
  function drawGrid(g){
   var box=S.$('dm-grid');
   if(!g){box.innerHTML='<p class="studio-note">这次结果还没有做参数检验。</p>';return;}
   var b=g.best||{},same=function(x){return x.lookback===b.lookback&&x.rebalance===b.rebalance&&x.threshold===b.threshold;};
   var rows=(g.rows||[]).slice().sort(function(x,y){return x.lookback-y.lookback||x.rebalance-y.rebalance||x.threshold-y.threshold;});
   box.innerHTML='<p class="studio-note">'+esc(g.message)+'</p><div class="studio-kpis"><span>样本外夏普为正的组合<b>'+pct(g.positive_test_share,0)+'</b></span><span>样本外跑赢同参数纯选股<b>'+pct(g.better_than_base_share,0)+'</b></span><span>训练期最佳在样本外排名<b>'+esc(g.best_test_rank)+' / '+rows.length+'</b></span></div>'+
    table(['动量窗口','调仓间隔','阈值','训练期夏普','样本外夏普','同参数纯选股样本外夏普','样本外夏普差','邻近参数样本外夏普','样本外收益','样本外回撤','平均仓位','年换手（倍）'],rows.map(function(x){return {cls:same(x)?'dm-picked':'',cells:[x.lookback+(same(x)?' ★':''),x.rebalance,x.threshold+'%',num(x.train_sharpe),tone(x.test_sharpe,num(x.test_sharpe)),num(x.base_test_sharpe),tone(x.delta_test_sharpe,signed(x.delta_test_sharpe,num)),num(x.neighbor_test_sharpe),pct(x.test_return),pct(x.test_drawdown),pct(x.avg_exposure,0),num(x.turnover,1)]};}))+
    '<p class="studio-note">★ 为训练期夏普最高的组合。'+esc(g.note||'')+'</p>';
  }
  function drawAI(a){
   var box=S.$('dm-ai');clearTimeout(timer);
   if(!a){box.innerHTML='<p class="studio-note">把这次的四组对照、增量检验和参数检验结果发给 HARNESS，请它检查结论是否站得住。AI 只读这些本机结果，回复是研究讨论，不构成投资建议。</p>';return;}
   var st={waiting:'已发送，等待回复…',partial:'正在写…',done:'已完成',timeout:'未取到回复',failed:'未发送'}[a.state]||a.state;
   box.innerHTML='<p class="studio-note">'+esc(String(a.sent_at||'').replace('T',' ').slice(0,16))+' · '+esc(st)+(a.message&&!a.reply?' · '+esc(a.message):'')+'</p>'+(a.reply?'<div class="dm-ai">'+text(a.reply)+'</div>':'')+'<p class="studio-note">完整对话在「AI 辅助」页的主会话。</p>';
   if(a.state==='waiting'||a.state==='partial'){var id=rec.id;timer=setTimeout(function(){LW.api.get('/api/studio/dual/detail?id='+encodeURIComponent(id),{bypass:true}).then(function(d){if(rec&&rec.id===id){rec.ai=d.record.ai;drawAI(rec.ai);}}).catch(function(){if(rec&&rec.id===id)drawAI(rec.ai);});},5000);}
  }
  function drawNotes(notes){
   var st=cfg?cfg.settings:{};notes=notes||st.notes||[];
   S.$('dm-notes').innerHTML='<h3>口径与局限</h3><ul class="studio-note">'+notes.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul>'+((st.references||[]).length?'<p class="studio-note">参考文献：'+st.references.map(function(x){return '<a target="_blank" rel="noopener" href="'+esc(x.url)+'">'+esc(x.title)+'</a>（'+esc(x.note)+'）';}).join('；')+'</p>':'');
  }

  S.$('dm-run').onclick=function(){var b=this,p=S.values('dm-form');b.disabled=true;S.job('dual_momentum',p,'dm-status',completed).catch(function(){}).finally(function(){b.disabled=false;});};
  S.$('dm-grid-run').onclick=function(){if(!rec)return;var b=this;b.disabled=true;S.job('dual_momentum_grid',{run_id:rec.id,lookbacks:S.$('dm-g-lb').value,rebalances:S.$('dm-g-rb').value,thresholds:S.$('dm-g-th').value},'dm-grid-status',completed).catch(function(){}).finally(function(){b.disabled=!rec;});};
  S.$('dm-ai-run').onclick=function(){if(!rec)return;var b=this;b.disabled=true;S.$('dm-ai-status').textContent='正在发送给 HARNESS…';LW.api.post('/api/studio/dual/ai',{id:rec.id}).then(function(d){S.$('dm-ai-status').textContent=d.message||'';rec.ai=d.record.ai;drawAI(rec.ai);overview(rec.id);}).catch(function(e){S.$('dm-ai-status').textContent=e.message;}).finally(function(){b.disabled=false;});};
  S.$('dm-open').onclick=function(){open(S.$('dm-history').value);};
  S.$('dm-grade').onchange=function(){drawHistory(rec&&rec.id);};
  overview().then(function(){
   S.recover('dm-status','dm-run',completed);S.recover('dm-grid-status','dm-grid-run',completed);
   var want=new URLSearchParams(location.search).get('dual');if(want&&runs.some(function(h){return h.id===want;}))S.$('dm-history').value=want;
   if(S.$('dm-history').value)open(S.$('dm-history').value);
  }).catch(function(e){S.$('dm-status').textContent='读取双层动量配置失败：'+e.message;});
 }
 LW.dualMomentum={mount:mount};
})();
