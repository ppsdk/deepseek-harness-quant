/* Unified chart controls. Catalogs, presets and limits come from research/config. */
(function(){
 'use strict';window.LW=window.LW||{};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 function shell(){return '<div class="chart-config-toolbar"><label for="chart-preset">图表组合<select id="chart-preset" disabled><option value="">读取配置…</option></select></label><button type="button" class="studio-button" id="chart-config-toggle" aria-expanded="false" aria-controls="chart-config-panel" disabled>配置指标 <span id="chart-config-count"></span><span class="chart-config-chevron" aria-hidden="true">⌄</span></button></div>'+ 
  '<div id="chart-selected" class="chart-selected" aria-label="已选指标"></div><div id="chart-config-panel" hidden><div class="chart-config-tabs" role="tablist" aria-label="指标配置方式"><button type="button" id="chart-tab-select" role="tab" aria-selected="true" aria-controls="chart-panel-select" tabindex="0">选择指标</button><button type="button" id="chart-tab-settings" role="tab" aria-selected="false" aria-controls="chart-panel-settings" tabindex="-1">参数与叠加</button></div>'+ 
  '<div id="chart-panel-select" role="tabpanel" aria-labelledby="chart-tab-select"><div class="chart-config-search"><label for="chart-indicator-search">搜索指标<input id="chart-indicator-search" type="search" placeholder="输入指标名称" aria-controls="chart-indicators"></label><span class="status-note" id="chart-guide"></span></div><div id="chart-indicators" class="indicator-picks" aria-label="图表指标"></div><p class="status-note" id="chart-selection-note" role="status"></p></div>'+ 
  '<div id="chart-panel-settings" class="chart-config-settings" role="tabpanel" aria-labelledby="chart-tab-settings" hidden><section><h3>指标参数 <span>计算周期可修改</span></h3><div id="chart-indicator-settings"></div></section><section><h3>同一指标追加参数 <span id="chart-overlay-count"></span></h3><div id="chart-overlays"></div></section></div></div>';}
 LW.chartControls={mount:function(host,config,prefs,onChange){
  if(typeof host==='string')host=document.getElementById(host);
  host.classList.add('chart-config');host.innerHTML=shell();
  prefs=prefs||{};
  var $=function(id){return host.querySelector('#'+id);};
  var catalog=config.factor_catalog||{},settings=config.chart_settings||{},presets=settings.pattern_presets||{};
  var groups=config.indicator_controls||{},familyRules=settings.indicator_families||{},labels=settings.indicator_labels||{};
  var families={},familyFor={},originalIds=Object.keys(catalog).filter(function(id){return catalog[id].chart;});
  originalIds.forEach(function(id){
   var factor=catalog[id],rule=factor.kind==='technical'&&familyRules[factor.operation],key=factor.kind==='technical'&&groupFor(id)?'operation:'+factor.operation:'factor:'+id;
   if(!families[key])families[key]={base:rule&&catalog[rule.base]&&catalog[rule.base].chart?rule.base:id,label:rule&&rule.label||labels[factor.operation]||factor.label,members:[]};
   families[key].members.push(id);familyFor[id]=families[key];
  });
  var ids=Object.keys(families).map(function(key){return families[key].base;});
  function groupFor(id){return Object.keys(groups).find(function(key){return groups[key].factors.includes(id);});}
  function parameters(id,overrides){
   var key=groupFor(id),fields=key?groups[key].fields:{},values={};
   Object.keys(fields).forEach(function(field){values[field]=fields[field].default;});
   return Object.assign(values,catalog[id].params||{},overrides||{});
  }
  var viewConfig=JSON.parse(JSON.stringify(config)),state=JSON.parse(JSON.stringify(prefs.indicator_params||{}));
  ids.forEach(function(id){
   var family=familyFor[id],key=groupFor(id);viewConfig.factor_catalog[id].label=family.label;
   if(key&&groups[key].factors.every(function(member){return !catalog[member]||catalog[member].operation===catalog[id].operation;}))viewConfig.indicator_controls[key].label=family.label;
  });
  var initial=Array.isArray(prefs.indicators)?prefs.indicators:settings.default_indicators||[];
  var selected=new Set(),extra=[],primary={};
  initial.filter(function(id){return familyFor[id];}).forEach(function(id){
   var base=familyFor[id].base,params=parameters(id,state[groupFor(id)]);
   if(selected.has(base))extra.push({base:base,params:params});
   else{selected.add(base);primary[base]=params;}
  });
  // Migrate existing MA20/MA60 selections to one MA type and two parameter instances.
  Object.keys(primary).forEach(function(base){var key=groupFor(base);if(key)state[key]=primary[base];});
  (Array.isArray(prefs.overlays)?prefs.overlays:[]).forEach(function(item){if(item&&familyFor[item.base])extra.push({base:familyFor[item.base].base,params:parameters(item.base,item.params)});});
  extra=extra.filter(function(item,index,items){
   var signature=JSON.stringify(item.params);
   return !(primary[item.base]&&JSON.stringify(primary[item.base])===signature)&&!items.slice(0,index).some(function(prev){return prev.base===item.base&&JSON.stringify(prev.params)===signature;});
  });
  // An overlay-only family still has a primary parameter instance in the UI.
  extra=extra.filter(function(item){
   if(selected.has(item.base)||selected.size>=settings.max_indicators)return true;
   selected.add(item.base);primary[item.base]=item.params;
   var key=groupFor(item.base);if(key)state[key]=item.params;return false;
  });
  Object.values(viewConfig.chart_settings.overlay_presets||{}).forEach(function(preset){
   preset.items=(preset.items||[]).filter(function(item){return familyFor[item.base];}).map(function(item){return {base:familyFor[item.base].base,params:parameters(item.base,item.params)};});
  });
  var editor,overlays;
  function names(){return ids.filter(function(id){return selected.has(id);});}
  function changed(){if(onChange)onChange();}
  function instanceLabel(id,params){
   var key=groupFor(id),fields=key?groups[key].fields:{};
   var values=parameters(id,params);
   return familyFor[id].label+(Object.keys(fields).length?' ('+Object.keys(fields).map(function(field){return values[field];}).join(' / ')+')':'');
  }
  function summary(){
   var count=overlays?overlays.get().length:0;
   var values=editor?editor.get():{};
   var added=overlays?overlays.get():[];
   $('chart-config-count').textContent=selected.size+' 类 · '+count+' 组追加参数';
   $('chart-overlay-count').textContent=count+' / '+settings.max_overlays;
   $('chart-selected').innerHTML=names().map(function(id){
    var label=instanceLabel(id,values[groupFor(id)]),key=groupFor(id),fields=key?Object.keys(groups[key].fields):[];
    var variants=added.filter(function(item){return item.base===id;});
    if(variants.length&&fields.length)label=familyFor[id].label+' ('+[parameters(id,values[key])].concat(variants.map(function(item){return item.params;})).map(function(params){return fields.map(function(field){return parameters(id,params)[field];}).join(' / ');}).join('；')+')';
    return '<button type="button" class="chart-selected-chip" data-chart-remove="'+esc(id)+'" aria-label="移除 '+esc(familyFor[id].label)+'">'+esc(label)+' <span aria-hidden="true">×</span></button>';
   }).join('')+
    (count?'<button type="button" class="chart-overlay-link" data-chart-show-overlays>追加参数 '+count+' 组 · 管理</button>':'')+
    (!selected.size&&!count?'<span class="status-note">未选择指标，仅显示K线</span>':'');
  }
  function sync(){
   $('chart-indicators').querySelectorAll('input').forEach(function(input){input.checked=selected.has(input.value);});
   editor.setFactors(names());
   summary();
  }
  function overlaysChanged(){
   var values=editor.get(),adopted=false;
   var remaining=overlays.get().filter(function(item){
    if(!selected.has(item.base)&&selected.size<settings.max_indicators){
     selected.add(item.base);values[groupFor(item.base)]=item.params;adopted=true;return false;
    }
    return !selected.has(item.base)||JSON.stringify(parameters(item.base,values[groupFor(item.base)]))!==JSON.stringify(parameters(item.base,item.params));
   });
   if(adopted)editor.setFactors(names(),values);
   overlays.set(remaining);sync();changed();
  }
  function tab(name,focus){
   ['select','settings'].forEach(function(key){
    var active=key===name,button=$('chart-tab-'+key);
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    $('chart-panel-'+key).hidden=!active;
    if(active&&focus)button.focus();
   });
  }
  $('chart-preset').innerHTML='<option value="">自选指标</option>'+Object.keys(presets).map(function(key){return '<option value="'+esc(key)+'">'+esc(presets[key].label)+'</option>';}).join('');
  $('chart-indicators').innerHTML=ids.map(function(id){return '<label title="'+esc(catalog[id].description||familyFor[id].label)+'"><input type="checkbox" value="'+esc(id)+'"> <span>'+esc(familyFor[id].label)+'</span></label>';}).join('');
  editor=LW.indicatorEditor.mount($('chart-indicator-settings'),viewConfig,names(),state,function(){summary();changed();},{embedded:true});
  overlays=LW.overlayEditor.mount($('chart-overlays'),viewConfig,extra,overlaysChanged,{embedded:true,bases:ids});
  $('chart-guide').textContent='同类指标只选一次，周期在「参数与叠加」中设置；最多 '+settings.max_indicators+' 类。';
  $('chart-config-toggle').disabled=false;$('chart-preset').disabled=false;
  $('chart-config-toggle').onclick=function(){var open=this.getAttribute('aria-expanded')!=='true';this.setAttribute('aria-expanded',String(open));$('chart-config-panel').hidden=!open;};
  host.querySelector('[role="tablist"]').onclick=function(e){var button=e.target.closest('[role="tab"]');if(button)tab(button.id==='chart-tab-select'?'select':'settings');};
  host.querySelector('[role="tablist"]').onkeydown=function(e){
   if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
   e.preventDefault();tab(e.key==='Home'?'select':e.key==='End'?'settings':$('chart-tab-select').getAttribute('aria-selected')==='true'?'settings':'select',true);
  };
  $('chart-indicator-search').oninput=function(){
   var query=this.value.trim().toLocaleLowerCase(),visible=0;
   $('chart-indicators').querySelectorAll('label').forEach(function(label){label.hidden=!label.textContent.toLocaleLowerCase().includes(query);if(!label.hidden)visible++;});
   $('chart-selection-note').textContent=visible?'':'没有匹配的指标，请换个关键词。';
  };
  $('chart-indicators').onchange=function(e){
   var input=e.target;if(input.type!=='checkbox')return;
   if(input.checked&&selected.size>=settings.max_indicators){input.checked=false;$('chart-selection-note').textContent='最多选择 '+settings.max_indicators+' 项，请先移除一项。';return;}
   if(input.checked)selected.add(input.value);else{selected.delete(input.value);overlays.set(overlays.get().filter(function(item){return item.base!==input.value;}));}
   $('chart-selection-note').textContent='';$('chart-preset').value='';sync();changed();
  };
  $('chart-preset').onchange=function(){
   var preset=presets[this.value];if(!preset)return;
   selected=new Set(preset.indicators.filter(function(id){return familyFor[id];}).map(function(id){return familyFor[id].base;}));
   $('chart-selection-note').textContent='';sync();changed();
  };
  $('chart-selected').onclick=function(e){
   var remove=e.target.closest('[data-chart-remove]');
   if(remove){selected.delete(remove.dataset.chartRemove);overlays.set(overlays.get().filter(function(item){return item.base!==remove.dataset.chartRemove;}));$('chart-preset').value='';$('chart-selection-note').textContent='';sync();changed();}
   if(e.target.closest('[data-chart-show-overlays]')){$('chart-config-panel').hidden=false;$('chart-config-toggle').setAttribute('aria-expanded','true');tab('settings',true);}
  };
  sync();
  return {getIndicators:names,getParams:editor.get,getOverlays:overlays.get,setParams:function(params){editor.setFactors(names(),params);summary();},decorate:function(data){
   Object.keys(data.indicators||{}).forEach(function(id){
    if(familyFor[id]&&catalog[id].kind==='technical')data.indicators[id].label=instanceLabel(id,(data.indicator_params||{})[groupFor(id)]);
   });
   (data.parameter_summary||[]).forEach(function(item){if(viewConfig.indicator_controls[item.id])item.label=viewConfig.indicator_controls[item.id].label;});
   return data;
  }};
 }};
})();
