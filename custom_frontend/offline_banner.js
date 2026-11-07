/* =====================================================================
   木笔ReviewFlow · W5 PWA 离线只读快照提示条（2.1.4-r8，自包含模块）
   ---------------------------------------------------------------------
   治理红线：IIFE 自包含；十语言 RFLang.register（列序与主词典一致：
   zh-CN/en/fr/ru/es/ja/pt/de/sr/ko）；一律经 textContent 渲染文本节点，
   不做任何 HTML 字符串拼接；独立容错（本模块任何失败不影响应用其余部分）。
   行为：页面就绪后向 Service Worker 查询「本次文档是否由离线快照兜底」，
   是则在页面顶部显示只读快照提示条；恢复联网（online 事件）自动收起并清旗。
   不做离线编辑（与 docs/MOBILE_INTERFACE.md 口径一致）：仅如实提示，不拦截
   任何操作——离线写请求由应用既有 apiRequest 错误处理优雅降级。
   结构约定：RFLang.register 必须是 IIFE 体内无花括号包裹的顶层语句——
   test_r7_frontsec_fixes._replay_dictionary 按「文件前缀＋})();」切词回放，
   包进 try/if 花括号会让词典回放钉子解析失败。
   ===================================================================== */
(function () {
  'use strict';

  var ROWS = [
    ['当前离线：显示的是上次在线时的页面外壳，任务与文献数据需恢复连接后加载。',
     'You are offline: this is the page shell from your last online session; task and reference data load once the connection is restored.',
     'Vous êtes hors ligne : ceci est l’enveloppe de page de votre dernière session en ligne ; les données de tâches et de références se chargent au retour de la connexion.',
     'Вы не в сети: показана оболочка страницы из последнего онлайн-сеанса; данные задач и источников загрузятся после восстановления соединения.',
     'Sin conexión: esta es la estructura de página de su última sesión en línea; los datos de tareas y referencias se cargan al restablecerse la conexión.',
     'オフラインです。表示しているのは前回オンライン時のページ枠で、タスクと文献のデータは接続の復帰後に読み込まれます。',
     'Sem conexão: esta é a estrutura da página da sua última sessão online; os dados de tarefas e referências carregam quando a conexão for restabelecida.',
     'Sie sind offline: Dies ist das Seitengerüst Ihrer letzten Online-Sitzung; Aufgaben- und Literaturdaten werden nach Wiederherstellung der Verbindung geladen.',
     'Нисте на вези: приказан је оквир странице из последње онлајн сесије; подаци задатака и извора се учитавају поновном успоставом везе.',
     '오프라인 상태입니다. 마지막 온라인 세션의 페이지 틀이 표시되며, 작업과 문헌 데이터는 연결이 복구되면 불러옵니다.'],
    ['页面外壳来自最近一次在线时的缓存；离线期间不提供编辑与同步。',
     'The page shell comes from the cache of your last online session; editing and syncing are unavailable while offline.',
     'L’enveloppe de page provient du cache de votre dernière session en ligne ; la modification et la synchronisation sont indisponibles hors ligne.',
     'Оболочка страницы взята из кэша последнего онлайн-сеанса; правка и синхронизация офлайн недоступны.',
     'La estructura de página proviene de la caché de su última sesión en línea; la edición y la sincronización no están disponibles sin conexión.',
     'ページ枠は前回オンライン時のキャッシュです。オフライン中は編集と同期は利用できません。',
     'A estrutura da página vem do cache da sua última sessão online; edição e sincronização não estão disponíveis offline.',
     'Das Seitengerüst stammt aus dem Cache Ihrer letzten Online-Sitzung; Bearbeiten und Synchronisieren sind offline nicht verfügbar.',
     'Оквир странице потиче из кеша последње онлајн сесије; уређивање и синхронизација нису доступни без везе.',
     '페이지 틀은 마지막 온라인 세션의 캐시에서 제공되며, 오프라인 중에는 편집과 동기화를 사용할 수 없습니다.'],
    ['当前离线，无法校验登录状态；页面外壳来自上次在线时的缓存，数据与登录需恢复连接。',
     'You are offline, so your sign-in state cannot be verified. The page shell comes from your last online session; data and sign-in need the connection back.',
     'Vous êtes hors ligne, votre état de connexion ne peut pas être vérifié. L’enveloppe de page provient de votre dernière session en ligne ; les données et la connexion nécessitent le rétablissement du réseau.',
     'Вы не в сети, поэтому состояние входа проверить нельзя. Оболочка страницы взята из последнего онлайн-сеанса; данные и вход требуют восстановления соединения.',
     'Sin conexión, no se puede verificar el estado de la sesión. La estructura de página proviene de su última sesión en línea; los datos y el inicio de sesión necesitan la conexión.',
     'オフラインのためログイン状態を確認できません。ページ枠は前回オンライン時のキャッシュで、データとログインには接続の復帰が必要です。',
     'Sem conexão, não é possível verificar o estado de login. A estrutura da página vem da última sessão online; os dados e o login precisam da conexão de volta.',
     'Sie sind offline, daher kann der Anmeldestatus nicht geprüft werden. Das Seitengerüst stammt aus Ihrer letzten Online-Sitzung; Daten und Anmeldung benötigen die Verbindung.',
     'Нисте на вези, па стање пријаве није могуће проверити. Оквир странице потиче из последње онлајн сесије; подаци и пријава захтевају поновну везу.',
     '오프라인이므로 로그인 상태를 확인할 수 없습니다. 페이지 틀은 마지막 온라인 세션의 캐시이며, 데이터와 로그인에는 연결이 필요합니다.'],
    ['隐藏此提示',
     'Hide this notice',
     'Masquer cet avis',
     'Скрыть это уведомление',
     'Ocultar este aviso',
     'この通知を隠す',
     'Ocultar este aviso',
     'Diesen Hinweis ausblenden',
     'Сакриј ово обавештење',
     '이 알림 숨기기']
  ];

  /* zh 键从词条行派生（单一事实来源；词条行用字面量，词典扫描器可解析）。 */
  var MSG_KEY = ROWS[0][0];
  var HINT_KEY = ROWS[1][0];
  var LOGIN_NOTE_KEY = ROWS[2][0];
  var CLOSE_KEY = ROWS[3][0];

  var BANNER_ID = 'rfOfflineSnapshotBanner';
  var NOTE_ID = 'rfOfflineStartupNote';

  function text(key) {
    try {
      return (typeof RFLang !== 'undefined' && RFLang && typeof RFLang.t === 'function')
        ? RFLang.t(key) : key;
    } catch (e) {
      return key;
    }
  }

  /* ---- 提示条：固定高度的细条，绝不吃掉视口 ----
     第 3 轮评审 high：旧实现用 position:sticky + width:100% 却没有 height/
     flex:none，而 index.html 的 body{display:flex;height:100vh;overflow:hidden}
     （默认 align-items:stretch）把这个 block 子项的高度拉到 100vh——提示条
     变成覆盖全屏的实心色块，把登录框整块盖住且点不到。改为 position:fixed
     顶部细条：脱离 body 的 flex 主轴/交叉轴，高度由内容决定。
     第 4 轮评审 medium（本组）：条仍以 z-index 高于登录层的方式压在登录页顶部，
     而 .site-header（语言/深色浅色/暂停动效三控件）就在 top:30px——三控件被条
     盖住点不到，与「不拦截任何操作」自查不符。修复＝让位而非遮挡：
       · 应用态：#appShell / #appMain（侧栏与主区顶部 .top 控制条）加顶部内边距；
       · 登录态：#authScreen .site-header 是 position:absolute（top 相对 padding
         box），给 #authScreen 加内边距挪不动它（第 3 轮那种补偿在登录页无效），
         故按其真实 rect 现算位移，下移到条底之下（移动端断点 top:20px 同构）；
       · 两者都存基线，hide() 精确复原原内联值。
     条始终只占「本来就空着」的顶部条带，不与任何可点控件重叠。 */
  var BAR_HEIGHT_PX = 0; /* show()/relayout() 实测的条高，供让位使用 */
  var GAP_PX = 4;        /* 条底与此后内容的呼吸间隙 */
  /* 应用态需要下移的容器（侧栏 + 主区）。登录层不在内：见下方 header 让位。 */
  var RESERVED = ['appShell', 'appMain'];
  var reservedBase = {}; /* id -> {inline: 原内联 padding-top} */
  var basePadding = {};  /* id -> 原始计算 padding-top（只记一次，重算基线） */
  /* 顶部贴合（position:fixed;top:Npx）的浮层控件：也是可点入口，条同样不能压。
     实测（第 4 轮自查）：#rfNavigationSheet 的侧栏开关是 fixed;top:8px;z-index:60，
     层级低于条，展开侧栏的入口会被整块盖住——与登录三控件是同一类缺陷。 */
  var TOP_ANCHORED = ['rfNavigationSheet'];
  var topAnchoredBase = {}; /* id -> 原内联 top（通常是空串，位置来自 CSS） */

  function show() {
    if (typeof document === 'undefined' || !document.body) return;
    /* 已显示时再收到一次快照事件（重连/重发）不重建条，但要让位跟着当前
       可见层重算——层可能已经切换过，幂等重算不会叠加。 */
    if (document.getElementById(BANNER_ID)) { relayout(); return; }
    var bar = document.createElement('div');
    bar.id = BANNER_ID;
    bar.setAttribute('role', 'status');
    bar.setAttribute('aria-live', 'polite');
    bar.setAttribute('data-rf-offline-banner', '1');
    /* 固定定位 + 显式 max-height + flex 不参与父级拉伸。 */
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483000;' +
      'display:flex;align-items:center;gap:10px;box-sizing:border-box;' +
      'width:auto;height:auto;max-height:40vh;overflow:auto;flex:0 0 auto;align-self:flex-start;' +
      'padding:6px 12px;background:#B0563F;color:#FFFFFF;' +
      'font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;';
    var body = document.createElement('div');
    body.style.cssText = 'display:flex;flex-direction:column;gap:1px;min-width:0;flex:1 1 auto;';
    var msg = document.createElement('strong');
    msg.textContent = text(MSG_KEY); /* 红线：textContent 渲染 */
    var hint = document.createElement('span');
    hint.textContent = text(HINT_KEY);
    hint.style.opacity = '0.9';
    body.appendChild(msg);
    body.appendChild(hint);
    bar.appendChild(body);
    /* 自助出口：色块/提示条绝不能是只能靠刷新才能消掉的死路。 */
    var close = document.createElement('button');
    close.type = 'button';
    close.id = BANNER_ID + 'Close';
    close.textContent = text(CLOSE_KEY);
    close.style.cssText = 'flex:0 0 auto;background:transparent;color:#FFFFFF;' +
      'border:1px solid rgba(255,255,255,.55);border-radius:4px;padding:2px 8px;' +
      'font:inherit;cursor:pointer;';
    close.addEventListener('click', function () { hide(); });
    bar.appendChild(close);
    document.body.appendChild(bar);
    /* 让位（不是遮挡）：先把条高测出来，再让应用壳/登录页顶部控制条整体
       下移到条底之下。条本身不动、层级不变，但不再覆盖任何可点控件。 */
    BAR_HEIGHT_PX = bar.getBoundingClientRect().height || 0;
    reserve(true);
    /* 页面若已由快照离线交付，可能等不到任何一次成功的 fetch；先在
       本地探一次托管回环（不新增外部出站：同源 /api/health）。 */
    probeBackend();
  }

  function loginVisible() {
    var host = document.getElementById('authScreen');
    return !!(host && host.style.display !== 'none');
  }

  /* 登录页控件让位：#authScreen .site-header 是 position:absolute;top:30px，
     top 相对 #authScreen 的 padding box——给 #authScreen 加内边距挪不动它
     （第 3 轮补偿在登录页从未生效，实测三控件仍被条压住）。故按 header 的
     真实 rect 现算：条底（margin-box 底 + 间隙）减去 header 的 border-box
     顶部，得到所需位移，用 transform 下移。transform 不改布局、不改内联
     于 #authScreen.style 的属性，也就不会触发登录层的 MutationObserver。
     宽视口（top:30px）与窄视口（top:20px）同构：都是现算，不写死断点。
     幂等：位移量按「当前 rect 反推出的原始 top」现算，再与已写入的位移比较，
     变了才写（否则 observe-写-再 observe 会自激）。反推而不是缓存基线，是因为
     CSS 断点会改 header 的 top（宽视口 30px / 窄视口 16px），缓存的旧基线
     会在跨断点时算出偏小的位移，让控制条上沿又滑回条下面。 */
  var headerShift = 0; /* 当前写进 header.style.transform 的位移（px），0=无 */
  var headerBase = null; /* 原内联 transform（用于收起时精确还原） */

  function reserveHeader(on) {
    var host = document.getElementById('authScreen');
    var header = host && host.querySelector ? host.querySelector('.site-header') : null;
    if (!header || !header.style) return;
    if (on) {
      if (typeof header.getBoundingClientRect !== 'function') return;
      var rect = header.getBoundingClientRect();
      var baseTop = (rect.top || 0) - headerShift; /* 反推 CSS 给的原始 top */
      if (!headerBase) headerBase = { transform: header.style.transform || '' };
      var want = Math.ceil((BAR_HEIGHT_PX + GAP_PX) - baseTop);
      if (want < 0) want = 0;
      headerShift = want;
      var next = want > 0 ? 'translateY(' + want + 'px)' : headerBase.transform;
      if ((header.style.transform || '') !== next) header.style.transform = next;
    } else if (headerBase) {
      header.style.transform = headerBase.transform;
      headerBase = null;
      headerShift = 0;
    }
  }

  /* 应用态让位：条是 fixed 的，会压住 #appShell（侧栏顶部）与 #appMain 的
     .top 控制条。给两者加「原计算内边距 + 条高」的内联 padding，收起时精确
     还原原值。注意（第 3 轮实测自查发现）：基线必须取计算值，读内联
     style.paddingTop 通常为空串，会把 CSS 内边距覆盖成一行高度。 */
  function reserveShell(on) {
    if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return;
    for (var i = 0; i < RESERVED.length; i++) {
      var id = RESERVED[i];
      var el = document.getElementById(id);
      if (!el || !el.style) continue;
      if (on) {
        if (!Object.prototype.hasOwnProperty.call(reservedBase, id)) {
          if (!basePadding[id]) {
            /* 只记一次原始计算值：之后的重算都以它为基线，重复调用不叠加。 */
            basePadding[id] = parseFloat(getComputedStyle(el).paddingTop) || 0;
          }
          reservedBase[id] = { inline: el.style.paddingTop || '' };
        }
        var target = Math.round((basePadding[id] || 0) + BAR_HEIGHT_PX + GAP_PX) + 'px';
        if (el.style.paddingTop !== target) el.style.paddingTop = target;
      } else if (Object.prototype.hasOwnProperty.call(reservedBase, id)) {
        el.style.paddingTop = reservedBase[id].inline;
        delete reservedBase[id];
        /* 释放后连基线一起丢掉：下一次预留时内联值已还原，getComputedStyle
           读到的是当前 CSS 值（媒体查询/密度切换改了内边距也不会用旧基线）。 */
        delete basePadding[id];
      }
    }
  }

  /* 顶部贴合的浮层控件让位：#rfNavigationSheet 是 fixed;top:8px（44×44 圆钮），
     层级低于条，会被整块盖住——展开侧栏的入口就此消失。这一类的正确做法是
     把它整体下移到条底之下（改内联 top），而不是靠提高层级去压条。
     两条约束：
       · 只在真的重叠时动它（rect.top < 条底）——窄屏断点把同一元素改成
         `top:auto!important;bottom:12px`（贴底），此时它本来就在条下方，
         不该被挪也不会被挪（!important 赢过内联 top）；用 overlap 判定，
         就与「它是怎么定位的」解耦，不写死断点。
       · 基线存原内联 top（一般为空串，位置来自 CSS），收起时原样写回。 */
  function reserveTopAnchored(on) {
    if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return;
    var barBottom = BAR_HEIGHT_PX + GAP_PX;
    for (var i = 0; i < TOP_ANCHORED.length; i++) {
      var id = TOP_ANCHORED[i];
      var el = document.getElementById(id);
      if (!el || !el.style) continue;
      if (on) {
        if (typeof el.getBoundingClientRect !== 'function') continue;
        var rect = el.getBoundingClientRect();
        if (!rect || !rect.height) continue;      /* 隐藏时不预留 */
        var tracked = Object.prototype.hasOwnProperty.call(topAnchoredBase, id);
        if (!tracked) {
          if (rect.top >= barBottom) continue;    /* 没被条压住：不动它 */
          topAnchoredBase[id] = {
            inline: el.style.top || '',
            base: parseFloat((getComputedStyle(el) || {}).top) || 0,
          };
        }
        /* 目标位置恒为条底（+间隙）：不累加、不依赖上一次的 rect，天然幂等。 */
        var target = Math.round(barBottom) + 'px';
        if ((el.style.top || '') !== target) el.style.top = target;
      } else if (Object.prototype.hasOwnProperty.call(topAnchoredBase, id)) {
        el.style.top = topAnchoredBase[id].inline;
        delete topAnchoredBase[id];
      }
    }
  }

  /* 登录态走 header 位移，应用态走容器内边距 + 浮层 top。
     幂等是硬要求（每条让位都会写内联样式，而写样式又会回调 relayout）：
     只在「可见层变了」时跨层释放/预留；同层只按当前条高刷新目标值，
     绝不无条件先释放再重设——那是 observe→写→再 observe 的自激源。
     三个 reserve*() 内部也只写与目标不同的值。 */
  var currentLayer = ''; /* '' | 'login' | 'app' */

  function reserve(on) {
    if (!on) {
      currentLayer = '';
      reserveHeader(false);
      reserveShell(false);
      reserveTopAnchored(false);
      return;
    }
    var layer = loginVisible() ? 'login' : 'app';
    if (layer !== currentLayer) {
      /* 跨层：先把另一层的让位还回去，再按新层预留（每层各自存基线）。 */
      if (layer === 'login') {
        reserveShell(false);
        reserveTopAnchored(false);
        reserveHeader(true);
      } else {
        reserveHeader(false);
        reserveShell(true);
        reserveTopAnchored(true);
      }
      currentLayer = layer;
      return;
    }
    /* 同层：只按最新条高刷新目标值（尺寸/换行变化后仍正确），不跨层释放。 */
    if (layer === 'login') reserveHeader(true);
    else { reserveShell(true); reserveTopAnchored(true); }
  }

  /* 恢复联网即收起：第 3 轮评审实测——后端在同一端口重新起来时 navigator
     .onLine 恒为 true、永远不会派发 online 事件，所以只靠 online 会让提示条
     永久滞留。这里的探测只打同源 /api/health（应用本就声明的前端-后端回环，
     零第三方出站），且失败完全静默。 */
  var PROBE_DELAYS_MS = [3000, 6000, 12000, 24000, 45000];
  var probeTimer = null;
  var probeRound = 0;

  function scheduleProbe() {
    clearTimeout(probeTimer);
    if (probeRound >= PROBE_DELAYS_MS.length) return;
    probeTimer = setTimeout(function () {
      probeRound++;
      probeBackend();
    }, PROBE_DELAYS_MS[probeRound]);
  }

  function probeBackend() {
    if (typeof fetch !== 'function') return;
    var ctl = { cache: 'no-store' };
    if (typeof AbortController === 'function') {
      ctl.signal = new AbortController().signal;
      setTimeout(function () { try { ctl.signal.abort(); } catch (e) {} }, 2500);
    }
    fetch('/api/health', ctl).then(function (resp) {
      /* 「后端在」的判定：/api/health 在本应用是免鉴权端点（main.py 顶部注释
         明列），所以只接受 2xx/3xx。4xx（尤其静态托管对任意路径回的 404）
         只说明「有东西应答」，不代表应用后端回来了——继续探测。 */
      if (resp && resp.status >= 200 && resp.status < 400) clearOfflineUi();
      else scheduleProbe();
    }).catch(function () {
      scheduleProbe();
    });
  }

  function hide() {
    if (typeof document === 'undefined') return;
    clearTimeout(probeTimer);
    probeRound = 0;
    reserve(false);
    var bar = document.getElementById(BANNER_ID);
    if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
    postToController('rf-leave-offline-snapshot');
  }

  /* ---- 登录页离线说明（原 index.html 内联块迁移至此）----
     第 3 轮评审 medium：内联块 append 到 #authScreen 后是 static 定位，被
     position:fixed;z-index:0 的 .login-bg 盖住（实测该区域 0 个可见像素），
     且只在 navigator.onLine === false 时创建——本地后端挂掉时 onLine 恒
     true，最常见的断连场景根本进不去；另外它永不清理，恢复联网后仍宣称
     离线。这里改为：进入登录态时按「最近一次连接结局」决定，样式带
     position:relative;z-index:6 保证绘制在背景之上，并在 hide()/登录成功/
     online 三处统一清理。 */
  function showLoginNote() {
    if (typeof document === 'undefined' || !document.body) return;
    var host = document.getElementById('authScreen');
    if (!host || host.style.display === 'none') return;
    if (document.getElementById(NOTE_ID)) return;
    var note = document.createElement('p');
    note.id = NOTE_ID;
    note.setAttribute('role', 'status');
    note.textContent = text(LOGIN_NOTE_KEY);
    note.style.cssText = 'position:relative;z-index:6;margin:10px auto 0;max-width:520px;' +
      'color:#B0563F;font-size:13px;line-height:1.5;text-align:center;';
    host.appendChild(note);
  }

  function clearLoginNote() {
    if (typeof document === 'undefined') return;
    var note = document.getElementById(NOTE_ID);
    if (note && note.parentNode) note.parentNode.removeChild(note);
  }

  function controller() {
    try {
      return (typeof navigator !== 'undefined' && navigator.serviceWorker)
        ? navigator.serviceWorker.controller : null;
    } catch (e) {
      return null;
    }
  }

  function postToController(type) {
    var ctl = controller();
    if (!ctl) return;
    try { ctl.postMessage({ type: type }); } catch (e) { /* 控制器已换代等：无碍 */ }
  }

  function onMessage(event) {
    var data = event && event.data;
    if (!data || typeof data.type !== 'string') return;
    if (data.type === 'rf-offline-snapshot') {
      snapshotDelivered = true;
      show();
      showLoginNote();
    } else if (data.type === 'rf-offline-snapshot-state') {
      if (data.offlineSnapshot) {
        snapshotDelivered = true;
        show();
        showLoginNote();
      } else {
        snapshotDelivered = false;
        hide();
        clearLoginNote();
      }    }
  }

  function querySnapshotState() {
    postToController('rf-query-offline-snapshot');
  }

  /* 登录成功 / 进入应用 / 主动登出：都意味着当前不是「离线只读」状态。 */
  function clearOfflineUi() {
    /* 顺序要紧（第 3 轮实测自查）：hide() 里的让位还原会改 #authScreen 的
       style 属性 → 触发下面那段 MutationObserver；若此时 snapshotDelivered
       仍为 true，观察器会把刚删掉的说明又建回来。故先置状态，再动 DOM。 */
    snapshotDelivered = false;
    hide();
    clearLoginNote();
  }

  /* 层切换（登录↔应用）与视口变化后重算让位：条还在时按其当前实测高度重新
     预留（换行会让条变高），条不在时什么也不做。 */
  function relayout() {
    var bar = document.getElementById(BANNER_ID);
    if (!bar) return;
    if (typeof bar.getBoundingClientRect === 'function') {
      BAR_HEIGHT_PX = bar.getBoundingClientRect().height || BAR_HEIGHT_PX;
    }
    reserve(true);
  }

  function init() {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    window.addEventListener('online', clearOfflineUi);
    window.addEventListener('resize', relayout);
    /* 登录屏出现（含 logout 后）时补说明；进入应用时清掉；两者都要重算让位。 */
    var host = document.getElementById('authScreen');
    if (host && typeof MutationObserver === 'function') {
      new MutationObserver(function () {
        if (host.style.display === 'none') { clearLoginNote(); relayout(); }
        else { if (snapshotDelivered) showLoginNote(); relayout(); }
      }).observe(host, { attributes: true, attributeFilter: ['style'] });
    }
    /* 顶部贴合浮层（侧栏开关）常常在条之后才出现/变大（sidebar_sheet.js 的
       refresh()/rebuild() 会改它的 hidden 与尺寸）：出现时补一次让位，否则
       它会在条下面露头。自身写 top 也会触发本观察器，但 reserve() 幂等
       （hasOwnProperty 守卫），不会来回抖。 */
    if (typeof MutationObserver === 'function') {
      for (var i = 0; i < TOP_ANCHORED.length; i++) {
        var anchoredEl = document.getElementById(TOP_ANCHORED[i]);
        if (!anchoredEl) continue;
        try {
          new MutationObserver(relayout).observe(anchoredEl, {
            attributes: true, attributeFilter: ['hidden', 'style', 'class'],
          });
        } catch (e) { /* 观察失败不影响其它让位路径 */ }
      }
    }
    if (typeof navigator === 'undefined' || !navigator.serviceWorker) return;
    navigator.serviceWorker.addEventListener('message', onMessage);
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', querySnapshotState);
    } else {
      querySnapshotState();
    }
  }

  /* 本次文档是否由离线快照兜底（决定登录屏是否补说明）。 */
  var snapshotDelivered = false;

  /* 独立容错：RFLang 缺席（异常加载顺序）时静默跳过注册，词条按 zh 原文显示。
     无花括号包裹的顶层语句（见文件头结构约定）。 */
  if (typeof RFLang !== 'undefined' && RFLang && typeof RFLang.register === 'function') RFLang.register(ROWS);

  init();
})();
