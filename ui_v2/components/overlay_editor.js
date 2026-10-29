/* Editable parameter instances and batches share the API catalogue. */
(function(){
 'use strict';window.LW=window.LW||{};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 LW.overlayEditor={mount:function(host,config,initial,onChange,options){
  if(typeof host==='string')host=document.getElementById(host);options=options||{};
  var catalog=config.factor_catalog||{},groups=config.indicator_controls||{},chart=config.chart_settings||{};
  var limit=chart.max_overlays,presets=chart.overlay_presets||{},items=Array.isArray(initial)?initial.slice():[],editing=null,draft=null;
  function fieldsOf(id){var out={};Object.keys(groups).some(function(key){if(groups[key].factors.includes(id)){out=groups[key].fields;return true;}});return out;}
  var bases=(options.bases||Object.keys(catalog)).filter(function(id){var f=catalog[id];return f&&f.chart&&f.available!==false&&Object.keys(fieldsOf(id)).length;});
  function defaults(id){var out={},fields=fieldsOf(id);Object.keys(fields).forEach(function(k){out[k]=fields[k].default;});return Object.assign(out,(catalog[id]||{}).params||{});}
  function signature(item){var p=Object.assign(defaults(item.base),item.params);return item.base+JSON.stringify(Object.keys(p).sort().map(function(k){return [k,p[k]];}));}
  function label(item){var fs=fieldsOf(item.base);return (catalog[item.base]||{}).label+' · '+Object.keys(item.params||{}).map(function(k){return ((fs[k]||{}).label||k).split('（')[0]+' '+item.params[k];}).join('，');}
  function capture(){var base=host.querySelector('[data-ov-base]');if(!base)return;var params={};host.querySelectorAll('[data-ov-field]').forEach(function(i){params[i.dataset.ovField]=Number(i.value);});draft={base:base.value,params:params,batch:host.querySelector('[data-ov-batch]').value};}
  function fields(id,values){
   var fs=fieldsOf(id),p=Object.assign(defaults(id),values||{});
   host.querySelector('[data-ov-params]').innerHTML=Object.keys(fs).map(function(k){var f=fs[k];return '<label>'+esc(f.label)+'<input type="number" data-ov-field="'+esc(k)+'" min="'+f.min+'" max="'+f.max+'" step="'+(f.step||'any')+'" value="'+esc(p[k])+'" required></label>';}).join('');
   host.querySelector('[data-ov-batch]').placeholder=Object.keys(fs).length===1&&fs.n?'例如 7,13,34,89':'例如 '+JSON.stringify([p,p]);
  }
  function validate(item){
   if(!bases.includes(item.base))throw new Error('请选择支持参数化的指标。');
   var fs=fieldsOf(item.base),p=Object.assign(defaults(item.base),item.params);
   Object.keys(item.params).forEach(function(k){var f=fs[k],v=p[k];if(!f||typeof v!=='number'||!Number.isFinite(v)||v<f.min||v>f.max||(f.integer&&!Number.isInteger(v)))throw new Error((f&&f.label||k)+'超出允许范围。');});
   if(p.fast!=null&&p.slow!=null&&p.fast>=p.slow)throw new Error('短周期必须小于长周期。');
   return {base:item.base,params:p};
  }
  function changed(){if(onChange)onChange(items.slice());}
  function commit(next){
   var added=next.map(validate),kept=items.filter(function(_,i){return i!==editing;});
   added.forEach(function(item){if(!kept.some(function(old){return signature(old)===signature(item);}))kept.push(item);});
   if(kept.length>limit)throw new Error('最多 '+limit+' 组参数，本次未添加，请减少批量条数。');
   items=kept;editing=null;capture();render();changed();
  }
  function render(){
   var opened=host.querySelector('details')?.open;host.classList.add('overlay-editor');
   var body='<p class="studio-note">选择任意可调指标并填写参数；支持技术指标和形态指标。常用组合只是示例，现有参数组也可编辑。</p>'+
    '<div class="studio-controls"><label>指标<select data-ov-base>'+bases.map(function(id){return '<option value="'+esc(id)+'">'+esc(catalog[id].label)+'</option>';}).join('')+'</select></label><span data-ov-params class="overlay-params"></span></div>'+
    '<label class="studio-note">批量参数（可选）<input class="studio-input" style="display:block;width:100%" data-ov-batch aria-label="批量参数"></label>'+
    '<p class="studio-note">单周期指标可输入任意周期，用逗号分隔；多参数指标可输入 JSON 数组。不填批量时只添加上面的参数。</p>'+
    '<div class="studio-actions"><button type="button" class="studio-button" data-ov-add'+(!bases.length?' disabled':'')+'>'+(editing==null?'添加参数组':'保存修改')+'</button>'+(editing!=null?'<button type="button" class="studio-button" data-ov-cancel>取消编辑</button>':'')+(items.length?'<button type="button" class="studio-button" data-ov-clear>清空叠加</button>':'')+'</div>'+
    (Object.keys(presets).length?'<div class="studio-note">常用示例（也可自行输入任意周期）</div><div class="studio-actions">'+Object.keys(presets).map(function(k){return '<button type="button" class="studio-button" data-ov-preset="'+esc(k)+'">'+esc(presets[k].label)+'</button>';}).join('')+'</div>':'')+
    '<div class="overlay-chips">'+items.map(function(item,i){return '<span class="overlay-chip">'+esc(label(item))+'<button type="button" data-ov-edit="'+i+'" aria-label="编辑参数组">编辑</button><button type="button" data-ov-remove="'+i+'" aria-label="移除">×</button></span>';}).join('')+'</div><p class="studio-note" role="status" data-ov-note></p>';
   host.innerHTML=options.embedded?body:'<details'+(opened||items.length?' open':'')+'><summary>指标参数实例 · '+items.length+' / '+limit+' 组</summary>'+body+'</details>';
   var id=draft&&bases.includes(draft.base)?draft.base:bases[0];if(id){host.querySelector('[data-ov-base]').value=id;fields(id,draft&&draft.params);host.querySelector('[data-ov-batch]').value=draft&&draft.batch||'';}
  }
  host.onchange=function(e){if(e.target.matches('[data-ov-base]')){editing=null;draft=null;fields(e.target.value);host.querySelector('[data-ov-batch]').value='';host.querySelector('[data-ov-add]').textContent='添加参数组';}};
  host.onclick=function(e){var t=e.target.closest('button');if(!t)return;try{
   if(t.matches('[data-ov-add]')){
    if(!Array.from(host.querySelectorAll('[data-ov-field]')).every(function(i){return i.reportValidity();}))return;
    capture();var batch=draft.batch.trim(),values;
    if(!batch)values=[draft.params];
    else if(Object.keys(fieldsOf(draft.base)).length===1&&fieldsOf(draft.base).n&&!batch.startsWith('['))values=batch.split(/[,，;；\s]+/).filter(Boolean).map(function(n){return {n:Number(n)};});
    else{values=JSON.parse(batch);if(!Array.isArray(values)||!values.length||values.some(function(v){return !v||Array.isArray(v)||typeof v!=='object';}))throw new Error('批量参数应为非空 JSON 对象数组。');}
    commit(values.map(function(params){return {base:draft.base,params:params};}));
   }
   if(t.dataset.ovEdit!=null){capture();editing=Number(t.dataset.ovEdit);draft={base:items[editing].base,params:items[editing].params,batch:''};render();host.querySelector('[data-ov-base]').focus();}
   if(t.dataset.ovRemove!=null){capture();items.splice(Number(t.dataset.ovRemove),1);editing=null;render();changed();}
   if(t.matches('[data-ov-cancel]')){editing=null;capture();render();}
   if(t.matches('[data-ov-clear]')){capture();items=[];editing=null;render();changed();}
   if(t.dataset.ovPreset){editing=null;commit(presets[t.dataset.ovPreset].items||[]);}
  }catch(error){host.querySelector('[data-ov-note]').textContent=error.message;}};
  render();return {get:function(){return items.slice();},set:function(next){capture();items=Array.isArray(next)?next.slice():[];render();}};
 }};
})();
