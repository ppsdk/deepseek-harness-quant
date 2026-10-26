/* Configuration-driven flat navigation, with stock autocomplete. */
(function(){
 window.LW=window.LW||{};
 var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
 LW.sidebar={render:function(elId,opts){
  var el=document.getElementById(elId);if(!el)return;opts=opts||{};
  if(!opts.navigation){el.textContent='加载导航…';LW.api.get('/api/navigation').then(function(data){LW.sidebar.render(elId,Object.assign({},opts,{navigation:data}));}).catch(function(e){el.textContent=e.message;});return;}
  if(!document.querySelector('link[href="/v2/assets/studio.css"]')){var link=document.createElement('link');link.rel='stylesheet';link.href='/v2/assets/studio.css';document.head.appendChild(link);}
  var nav=opts.navigation,icons=LW.icons||{},items=(nav.primary||[]).concat(nav.more||[]),groups=nav.groups||[{items:items}];
  el.innerHTML='<div class="sb"><div class="sb-brand"><span class="sb-brand-text">'+esc(nav.brand||'量化研究')+'</span></div><nav class="sb-nav">'+groups.map(function(group){return (group.label?'<div class="sb-section">'+esc(group.label)+'</div>':'')+(group.items||[]).map(function(it){return '<a class="sb-item '+(it.id===opts.active?'active':'')+'" href="'+esc(it.url)+'">'+(icons[it.ic]||'')+esc(it.label)+'</a>';}).join('');}).join('')+'</nav><div class="sb-search" id="'+elId+'-srch"></div><div class="sb-foot" id="'+elId+'-foot"><span class="sb-mini">只读取本机已下载的数据</span></div></div>';
  if(LW.search)LW.search.render(elId+'-srch','输入名称或代码自动匹配');
  document.dispatchEvent(new Event('lw:sidebar-ready'));
 }};
})();

/* ★2026-08-15 可调宽度侧栏：拖拽右侧手柄调宽（200-420px），宽度记忆 localStorage，
   --sb-w 联动 .sb 宽度 与 body 左偏移（app.css 同源变量） */
(function () {
  var KEY = 'quant.sb.w';
  var MIN = 200, MAX = 420, DEF = 256;
  function applyW(px) {
    var v = Math.max(MIN, Math.min(MAX, px));
    document.documentElement.style.setProperty('--sb-w', v + 'px');
    try { localStorage.setItem(KEY, String(v)); } catch (e) {}
    return v;
  }
  function initResize() {
    var sb = document.querySelector('.sb');
    if (!sb || sb.querySelector('.sb-resize')) return;
    var saved = null;
    try { saved = parseInt(localStorage.getItem(KEY), 10); } catch (e) {}
    if (saved && saved >= MIN && saved <= MAX) applyW(saved);
    var handle = document.createElement('div');
    handle.className = 'sb-resize';
    handle.title = '拖拽调整侧栏宽度';
    sb.appendChild(handle);
    handle.addEventListener('mousedown', function (ev) {
      ev.preventDefault();
      var startX = ev.clientX;
      var startW = sb.getBoundingClientRect().width;
      handle.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      function move(e2) { applyW(startW + (e2.clientX - startX)); }
      function up() {
        handle.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }
  document.addEventListener('lw:sidebar-ready', initResize);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initResize);
  else initResize();
})();

/* ★2026-08-15 侧栏折叠：折叠后右侧内容区全屏（body.sb-collapsed → padding-left:0 + 侧栏移出），
   brand 行折叠按钮 + 悬浮展开按钮，状态记忆 localStorage（quant.sb.collapsed）
   ★2026-08-16 重构：①幂等（同状态直接返回，不重复触发）②不覆盖 --sb-w（宽度记忆永不被破坏）
   ③内容区 padding 与侧栏 transform 同速过渡（动画同步流畅）④动画期间忽略再次点击（busy 锁） */
(function () {
  var COLL = 'quant.sb.collapsed';
  var busy = false;
  function setCollapsed(c) {
    c = !!c;
    if (busy) return;                                   // 动画中忽略重复触发
    if (document.body.classList.contains('sb-collapsed') === c) return;  // 幂等
    busy = true;
    document.body.classList.toggle('sb-collapsed', c);
    try { localStorage.setItem(COLL, c ? '1' : '0'); } catch (e) {}
    var open = document.getElementById('sb-toggle-open');
    if (open) open.style.display = c ? 'flex' : 'none';
    setTimeout(function () { busy = false; }, 260);     // 与 CSS transition .22s 对齐
  }
  function initFold() {
    var sb = document.querySelector('.sb');
    if (!sb || sb.querySelector('.sb-fold-btn')) return;
    var brand = sb.querySelector('.sb-brand');
    if (brand) {
      var foldBtn = document.createElement('button');
      foldBtn.className = 'sb-fold-btn';
      foldBtn.title = '折叠侧栏（内容区全屏）';
      foldBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M15 6l-6 6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      foldBtn.addEventListener('click', function () { setCollapsed(true); });
      brand.appendChild(foldBtn);
    }
    var open = document.getElementById('sb-toggle-open');
    if (!open) {
      open = document.createElement('button');
      open.id = 'sb-toggle-open';
      open.className = 'sb-toggle-open';
      open.title = '展开侧栏';
      open.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      open.addEventListener('click', function () { setCollapsed(false); });
      document.body.appendChild(open);
    }
    var saved = null;
    try { saved = localStorage.getItem(COLL); } catch (e) {}
    // 初始恢复直接应用（不经过 busy 锁，避免首帧闪烁）
    busy = true;
    var initiallyCollapsed = saved == null ? window.innerWidth < 800 : saved === '1';
    document.body.classList.toggle('sb-collapsed', initiallyCollapsed);
    open.style.display = initiallyCollapsed ? 'flex' : 'none';
    setTimeout(function () { busy = false; }, 260);
  }
  document.addEventListener('lw:sidebar-ready', initFold);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initFold);
  else initFold();
})();
