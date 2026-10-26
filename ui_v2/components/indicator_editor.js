/* One parameter editor for chart views, saved strategies and backtest requests. */
(function(){
 'use strict';window.LW=window.LW||{};
 var serial=0,esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 LW.indicatorEditor={mount:function(host,config,names,initial,onApply){
  if(typeof host==='string')host=document.getElementById(host);
  var groups=config.indicator_controls||{},state=JSON.parse(JSON.stringify(initial||{})),chosen=[],prefix='indicator-setting-'+(++serial)+'-';
  function expand(keys){var seen=new Set();function visit(k){if(seen.has(k))return;seen.add(k);var f=(config.factor_catalog||{})[k]||{};(f.dependencies||(f.recipe||{}).features||[]).forEach(visit);}keys.forEach(visit);return seen;}
  function capture(){host.querySelectorAll('[data-indicator-field]').forEach(function(input){var key=input.dataset.indicatorGroup,field=input.dataset.indicatorField,value=Number(input.value),original=groups[key].fields[field].default;state[key]=state[key]||{};if(value===original)delete state[key][field];else state[key][field]=value;if(!Object.keys(state[key]).length)delete state[key];});}
  function values(){capture();return JSON.parse(JSON.stringify(state));}
  function render(keys,overrides){
   var opened=host.querySelector('details')?.open;capture();if(overrides!==undefined)state=JSON.parse(JSON.stringify(overrides||{}));
   var selected=expand(keys);chosen=Object.keys(groups).filter(function(k){return groups[k].factors.some(function(f){return selected.has(f);});});
   host.classList.add('indicator-editor');
   host.innerHTML='<details'+(opened?' open':'')+'><summary>调整指标参数 · '+chosen.length+' 组</summary><p class="studio-note">这里调整计算周期和识别条件。修改后点击应用；同组的KDJ三线、MACD各线共用参数。名称是原始模板名，图表会另列本次修改。图表修改不会改变已经归档的成交。</p><div class="indicator-editor-groups">'+chosen.map(function(k){var group=groups[k];return '<fieldset><legend>'+esc(group.label)+'</legend>'+Object.keys(group.fields).map(function(f,i){var p=group.fields[f],id=prefix+k.replace(/[^a-z0-9_-]/gi,'-')+'-'+i;return '<label for="'+id+'">'+esc(p.label)+'<input id="'+id+'" type="number" min="'+p.min+'" max="'+p.max+'" step="'+(p.step||'any')+'" required data-indicator-group="'+esc(k)+'" data-indicator-field="'+esc(f)+'" value="'+esc(state[k]&&state[k][f]!=null?state[k][f]:p.default)+'"></label>';}).join('')+'</fieldset>';}).join('')+'</div>'+(chosen.length?'<div class="studio-actions"><button type="button" class="studio-button" data-indicator-apply>应用指标参数</button><button type="button" class="studio-button" data-indicator-reset>恢复默认参数</button></div>':'<p class="studio-note">当前所选项目没有可调数值参数。可以在因子管理中编写公式。</p>')+'<p class="studio-note" role="status" data-indicator-note></p></details>';
   var apply=host.querySelector('[data-indicator-apply]');if(apply)apply.onclick=function(){var inputs=Array.from(host.querySelectorAll('input'));if(!inputs.every(function(i){return i.reportValidity();}))return;var v=values();host.querySelector('[data-indicator-note]').textContent='本次参数已应用。';if(onApply)onApply(v);};
   var reset=host.querySelector('[data-indicator-reset]');if(reset)reset.onclick=function(){chosen.forEach(function(k){delete state[k];});host.querySelectorAll('[data-indicator-field]').forEach(function(i){i.value=groups[i.dataset.indicatorGroup].fields[i.dataset.indicatorField].default;});if(onApply)onApply(values());};
  }
  render(names||[],initial);return {get:values,setFactors:render};
 }};
})();
