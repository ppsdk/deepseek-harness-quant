/* 本机定时研究任务：任务、步骤目录、模板和运行记录均来自 /api/studio/automation。 */
(function(){'use strict';
 var host=document.getElementById('research-automation');if(!host)return;
 var $=function(id){return document.getElementById(id);};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 var data=null,editing=null,timer=null,openRun=null;
 var STATE={done:'完成',partial:'部分完成',failed:'未完成',running:'运行中',interrupted:'已中断'};
 function opts(map,selected,blank){return (blank?'<option value="">'+esc(blank)+'</option>':'')+Object.keys(map).map(function(k){return '<option value="'+esc(k)+'"'+(k===selected?' selected':'')+'>'+esc(map[k])+'</option>';}).join('');}
 function badge(state){return '<span class="auto-badge auto-'+esc(state)+'">'+esc(STATE[state]||state)+'</span>';}
 function status(text){$('auto-status').textContent=text;}

 function form(task){
  var s=data.settings,d=s.defaults,p=(task&&task.params)||{},steps=task?task.steps:[],days=task?task.weekdays:d.weekdays;
  editing=task?task.id:null;
  $('auto-form').innerHTML='<div class="studio-controls"><label>任务名称<input id="auto-name" maxlength="80" value="'+esc(task?task.name:'')+'" placeholder="例如：收盘后检查候选股"></label><label>执行时间<input id="auto-time" type="time" value="'+esc(task?task.time:d.time)+'"></label><label><input type="checkbox" id="auto-enabled"'+(!task||task.enabled?' checked':'')+'> 启用定时</label></div>'+
   '<fieldset class="auto-days"><legend>执行日</legend>'+Object.keys(s.weekdays).map(function(k){return '<label><input type="checkbox" data-day="'+k+'"'+(days.indexOf(Number(k))>=0?' checked':'')+'> '+esc(s.weekdays[k])+'</label>';}).join('')+'</fieldset>'+
   '<fieldset class="auto-steps"><legend>步骤（按下列顺序执行，后一步使用前一步结果）</legend>'+Object.keys(s.steps).map(function(k){var st=s.steps[k];return '<label><input type="checkbox" data-step="'+esc(k)+'"'+(steps.indexOf(k)>=0?' checked':'')+'> <b>'+esc(st.label)+'</b><small>'+esc(st.description)+'</small></label>';}).join('')+'</fieldset>'+
   '<div class="studio-controls" id="auto-params">'+
    '<label data-for="strategy">策略<select id="auto-strategy">'+opts(data.strategies,p.strategy||data.default_strategy)+'</select></label>'+
    '<label data-for="topn">选几只<input id="auto-topn" type="number" min="1" max="'+data.limits.topn+'" value="'+esc(p.topn||d.topn)+'"></label>'+
    '<label data-for="stocks">参与选股的股票数<input id="auto-stocks" type="number" min="1" max="'+data.limits.stocks+'" value="'+esc(p.stocks||d.stocks)+'"></label>'+
    '<label data-for="signal_plan_id">信号方案<select id="auto-plan">'+opts(Object.fromEntries(data.signal_plans.map(function(x){return [x.id,x.name+'（'+x.pool_name+'）'];})),p.signal_plan_id,'为新选名单按预设新建')+'</select></label>'+
    '<label data-for="signal_preset">条件预设<select id="auto-preset">'+opts(data.signal_presets,p.signal_preset)+'</select></label>'+
    '<label data-for="years">回测最近几年<input id="auto-years" type="number" min="1" max="'+data.limits.years+'" value="'+esc(p.years||d.years)+'"></label>'+
    '<label data-for="assistant_task">AI 复核内容<select id="auto-ai">'+opts(data.assistant_tasks,p.assistant_task||d.assistant_task)+'</select></label>'+
   '</div><div class="studio-actions"><button id="auto-save" class="studio-button">'+(task?'保存修改':'保存任务')+'</button>'+(task?'<button id="auto-cancel" class="studio-button">取消编辑</button>':'')+'</div>';
  function sync(){
   var chosen={};$('auto-form').querySelectorAll('[data-step]').forEach(function(i){chosen[i.dataset.step]=i.checked;});
   var need={strategy:chosen.screen_pool||chosen.backtest||chosen.ai_review,topn:chosen.screen_pool,stocks:chosen.screen_pool,
             signal_plan_id:chosen.signal_check,years:chosen.backtest,assistant_task:chosen.ai_review,
             signal_preset:chosen.signal_check&&!$('auto-plan').value};
   $('auto-params').querySelectorAll('[data-for]').forEach(function(l){l.hidden=!need[l.dataset.for];});
  }
  $('auto-form').onchange=sync;sync();
  $('auto-save').onclick=save;if(task)$('auto-cancel').onclick=function(){form(null);};
 }

 function collect(){
  var steps=[],days=[];
  $('auto-form').querySelectorAll('[data-step]:checked').forEach(function(i){steps.push(i.dataset.step);});
  $('auto-form').querySelectorAll('[data-day]:checked').forEach(function(i){days.push(Number(i.dataset.day));});
  var params={strategy:$('auto-strategy').value,topn:Number($('auto-topn').value),stocks:Number($('auto-stocks').value),
              signal_plan_id:$('auto-plan').value,signal_preset:$('auto-preset').value,years:Number($('auto-years').value),assistant_task:$('auto-ai').value};
  return {id:editing||undefined,name:$('auto-name').value,time:$('auto-time').value,enabled:$('auto-enabled').checked,weekdays:days,steps:steps,params:params};
 }
 function save(){var b=this;b.disabled=true;LW.api.post('/api/studio/automation/tasks',collect()).then(function(r){status(r.message);form(null);return load();}).catch(function(e){status(e.message);}).finally(function(){b.disabled=false;});}

 function tasks(){
  if(!data.tasks.length)return '<p class="studio-note">还没有任务。可以从下方模板开始，或自行勾选步骤。</p>';
  return '<div class="studio-scroll"><table class="studio-table"><thead><tr><th>任务</th><th>步骤</th><th>计划</th><th>上次运行</th><th>操作</th></tr></thead><tbody>'+data.tasks.map(function(t){
   var labels=t.steps.map(function(k){return data.settings.steps[k].label;}).join(' → ');
   var days=t.weekdays.map(function(d){return data.settings.weekdays[d];}).join('、');
   var last=t.last_run?badge(t.last_run.state)+' <small>'+esc(t.last_run.finished_at.replace('T',' ').slice(0,16))+'</small>':'<small>尚未运行</small>';
   return '<tr><td><b>'+esc(t.name)+'</b>'+(t.enabled?'':' <small>（已停用）</small>')+'</td><td><small>'+esc(labels)+'</small></td><td><small>'+esc(days)+' '+esc(t.time)+'<br>下次：'+esc(t.next_run||'不定时')+'</small></td><td>'+last+'</td><td class="auto-ops"><button class="studio-button" data-run="'+esc(t.id)+'">立即运行</button><button class="studio-button" data-edit="'+esc(t.id)+'">编辑</button><button class="studio-button" data-del="'+esc(t.id)+'">删除</button></td></tr>';
  }).join('')+'</tbody></table></div>';
 }
 function runs(){
  if(!data.runs.length)return '<p class="studio-note">还没有运行记录。</p>';
  return data.runs.map(function(r){
   return '<details class="auto-run"'+(r.id===openRun||r.state==='running'?' open':'')+' data-run-id="'+esc(r.id)+'"><summary>'+badge(r.state)+' <b>'+esc(r.task_name)+'</b> <small>'+esc(r.started_at.replace('T',' ').slice(0,16))+' · '+(r.trigger==='schedule'?'定时':'手动')+(r.message?' · '+esc(r.message):'')+'</small></summary><ol>'+r.steps.map(function(s){
    return '<li>'+badge(s.state)+' '+esc(s.label)+'：'+esc(s.message||'')+(s.items&&s.items.length?'<br><small>'+s.items.map(esc).join('、')+'</small>':'')+(s.link?' <a href="'+esc(s.link)+'">查看</a>':'')+'</li>';
   }).join('')+'</ol></details>';
  }).join('');
 }

 function render(){
  var sch=data.scheduler;
  host.innerHTML='<p class="studio-note">在本机按时执行固定步骤：更新市场、选股存池、检查信号、回测、发给 AI 复核。只做研究，不下单、不改持仓。程序需保持运行；关闭期间错过的时点不补跑过去的日子，当天重新打开后补跑一次。</p>'+
   '<p class="studio-note">调度'+(sch.active?'运行中':'未启动（请用 启动.cmd 打开平台）')+' · 本机时间 '+esc(sch.now)+(sch.running?' · 正在执行「'+esc(sch.running.task_name)+'」':'')+'</p>'+
   '<h3>我的任务</h3>'+tasks()+
   '<h3>新建或编辑</h3><div class="studio-actions">'+data.settings.templates.map(function(t){return '<button class="studio-button" data-template="'+esc(t.id)+'">模板：'+esc(t.label)+'</button>';}).join('')+'</div><div id="auto-form"></div>'+
   '<div id="auto-status" class="studio-note" role="status"></div><h3>最近运行</h3><div id="auto-runs">'+runs()+'</div>';
  form(editing?data.tasks.find(function(t){return t.id===editing;})||null:null);
 }

 host.addEventListener('click',function(e){
  var t=e.target,id;
  if((id=t.dataset.template)){var tpl=data.settings.templates.find(function(x){return x.id===id;});form(null);$('auto-name').value=tpl.label;
   if(tpl.time)$('auto-time').value=tpl.time;
   if(tpl.weekdays)$('auto-form').querySelectorAll('[data-day]').forEach(function(i){i.checked=tpl.weekdays.indexOf(Number(i.dataset.day))>=0;});
   $('auto-form').querySelectorAll('[data-step]').forEach(function(i){i.checked=tpl.steps.indexOf(i.dataset.step)>=0;});
   $('auto-form').dispatchEvent(new Event('change'));$('auto-name').focus();}
  if((id=t.dataset.edit)){form(data.tasks.find(function(x){return x.id===id;}));$('auto-name').focus();}
  if((id=t.dataset.del)&&confirm('删除这个任务？历史运行记录会保留。')){LW.api.post('/api/studio/automation/delete',{id:id}).then(function(r){status(r.message);if(editing===id)editing=null;return load();}).catch(function(e){status(e.message);});}
  if((id=t.dataset.run)){t.disabled=true;LW.api.post('/api/studio/automation/run',{id:id}).then(function(r){status(r.message);setTimeout(load,800);}).catch(function(e){status(e.message);t.disabled=false;});}
 });
 host.addEventListener('toggle',function(e){if(e.target.dataset&&e.target.dataset.runId&&e.target.open)openRun=e.target.dataset.runId;},true);

 function load(){
  if(timer)clearTimeout(timer);
  return LW.api.get('/api/studio/automation',{bypass:true}).then(function(d){
   var busyForm=data&&document.activeElement&&$('auto-form')&&$('auto-form').contains(document.activeElement);
   data=d;
   if(busyForm){$('auto-runs').innerHTML=runs();}else render();   // 正在填写时只刷新运行记录
   timer=setTimeout(load,d.scheduler.running?5000:30000);
  }).catch(function(e){host.textContent='自动化任务未加载：'+e.message;timer=setTimeout(load,15000);});
 }
 load();
})();
