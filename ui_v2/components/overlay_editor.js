/* 同一技术指标按不同参数反复叠加（MA20 + MA30 + MA120、RSI6 + RSI24…）。
   可选指标、参数范围、默认值、常用组合都来自 /api/research/config。 */
(function(){
 'use strict';window.LW=window.LW||{};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 LW.overlayEditor={mount:function(host,config,initial,onChange){
  if(typeof host==='string')host=document.getElementById(host);
  var catalog=config.factor_catalog||{},groups=config.indicator_controls||{},chart=config.chart_settings||{};
  var limit=chart.max_overlays||8,presets=chart.overlay_presets||{},items=Array.isArray(initial)?initial.slice(0,limit):[];
  // 每个指标可调的参数：取它所在参数组的字段说明（标签、范围、步长）。
  function fieldsOf(id){var out=null;Object.keys(groups).some(function(k){if(groups[k].factors.indexOf(id)>=0){out=groups[k].fields;return true;}});return out;}
  var bases=Object.keys(catalog).filter(function(id){var f=catalog[id];return f.chart&&f.kind==='technical'&&f.available!==false&&fieldsOf(id);});
  function label(item){var f=catalog[item.base]||{},p=item.params||{};return (f.label||item.base)+' · '+Object.keys(p).map(function(k){var fs=fieldsOf(item.base)||{};return ((fs[k]||{}).label||k).split('（')[0]+' '+p[k];}).join('，');}
  function changed(){if(onChange)onChange(items.slice());}
  function inputs(){
   var id=host.querySelector('[data-ov-base]').value,fields=fieldsOf(id)||{},defaults=(catalog[id]||{}).params||{};
   host.querySelector('[data-ov-params]').innerHTML=Object.keys(fields).map(function(k){var f=fields[k];return '<label>'+esc(f.label)+'<input type="number" data-ov-field="'+esc(k)+'" min="'+f.min+'" max="'+f.max+'" step="'+(f.step||'any')+'" value="'+esc(defaults[k]!=null?defaults[k]:f.default)+'" required></label>';}).join('');
  }
  function render(){
   host.classList.add('overlay-editor');
   host.innerHTML='<details'+(items.length?' open':'')+'><summary>叠加同一指标（不同参数）· '+items.length+' / '+limit+' 条</summary>'+
    '<p class="studio-note">先选指标、改参数，再点「叠加到图上」；同一指标可以加多次，比如在20日均线之外再叠30日和120日。均线类画在主图，RSI等画在原指标的同一副图。</p>'+
    (Object.keys(presets).length?'<div class="studio-actions">'+Object.keys(presets).map(function(k){return '<button type="button" class="studio-button" data-ov-preset="'+esc(k)+'">'+esc(presets[k].label)+'</button>';}).join('')+'</div>':'')+
    '<div class="studio-controls"><label>指标<select data-ov-base>'+bases.map(function(id){return '<option value="'+esc(id)+'">'+esc(catalog[id].label)+'</option>';}).join('')+'</select></label><span data-ov-params class="overlay-params"></span><button type="button" class="studio-button" data-ov-add>叠加到图上</button>'+(items.length?'<button type="button" class="studio-button" data-ov-clear>清空叠加</button>':'')+'</div>'+
    '<div class="overlay-chips">'+items.map(function(it,i){return '<span class="overlay-chip">'+esc(label(it))+'<button type="button" aria-label="移除" data-ov-remove="'+i+'">×</button></span>';}).join('')+'</div>'+
    '<p class="studio-note" role="status" data-ov-note></p></details>';
   if(bases.length)inputs();
  }
  // 用属性赋值而不是 addEventListener：页面重新加载配置时会再次 mount，避免重复绑定。
  host.onchange=function(e){if(e.target.matches('[data-ov-base]'))inputs();};
  host.onclick=function(e){
   var t=e.target,note=host.querySelector('[data-ov-note]');
   if(t.matches('[data-ov-add]')){
    var fields=Array.from(host.querySelectorAll('[data-ov-field]'));if(!fields.every(function(i){return i.reportValidity();}))return;
    if(items.length>=limit){note.textContent='最多叠加 '+limit+' 条，请先移除几条。';return;}
    var params={};fields.forEach(function(i){params[i.dataset.ovField]=Number(i.value);});
    var item={base:host.querySelector('[data-ov-base]').value,params:params};
    if(items.some(function(x){return JSON.stringify(x)===JSON.stringify(item);})){note.textContent='这条已经叠加过了。';return;}
    items.push(item);render();changed();
   }
   if(t.dataset.ovRemove!=null){items.splice(Number(t.dataset.ovRemove),1);render();changed();}
   if(t.matches('[data-ov-clear]')){items=[];render();changed();}
   if(t.dataset.ovPreset){var p=presets[t.dataset.ovPreset];var add=(p.items||[]).filter(function(x){return catalog[x.base]&&!items.some(function(y){return JSON.stringify(y)===JSON.stringify(x);});});items=items.concat(add).slice(0,limit);render();changed();}
  };
  render();
  return {get:function(){return items.slice();},set:function(next){items=Array.isArray(next)?next.slice(0,limit):[];render();}};
 }};
})();
