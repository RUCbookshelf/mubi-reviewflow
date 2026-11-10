/* ReviewFlow · 研究设计分类徽标 + 排除理由预填（W4 预置分类器 · B3；2.1.4-r8）。
 *
 * 后端：custom_backend/study_design_api.py（GET /api/tasks/{id}/study-designs，
 * 纯本地正则启发式 coscreen/al/study_design.py，复用 evidence_mapper 规则）。
 * 本模块零后端改动、零新增依赖。
 *
 * 结构（追加式接入，与主应用互不影响——同 ai_suggest.js 范式）：
 *   - 十语言词条自包含注册（RFLang.register，中文为源串，
 *     zh-CN/en/fr/ru/es/ja/pt/de/sr/ko；设计类别显示名复用全局词典既有词条）；
 *   - 样式随模块注入（徽标族与 ai_suggest 的 .rf-ai-badge 同规格）；
 *   - 初始化与全部回调自捕获异常（单点故障：分类不可用 → 徽标消失、预填跳过，
 *     初筛流程不受影响，绝不抛错、绝不重试风暴）；
 *   - 数据一次拉取、按任务+用户+筛选员作用域缓存；语言切换事件重绘。
 *
 * 渲染红线：所有动态数据经 el()/textContent/setAttribute，绝不 innerHTML。
 * 网络红线：仅经全局 api() 访问本应用端点，无任何外部/回环地址。
 *
 * 「预填不代存」：判为非 RCT 的已识别设计时，把「研究设计不符」填入
 * #excReason 下拉（值=选项值）并派发 change——沿用 ai_suggest.js 采纳＝
 * 填控件模式（:533-560 先例）；绝不调用保存、绝不写决定；用户改过则不打扰。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' ||
        typeof el !== 'function' || typeof api !== 'function' ||
        typeof RFLang === 'undefined') return;

    /* =====================================================================
     * 一、十语言词条（模块自包含；列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko。
     *     设计类别显示名（随机对照试验/系统综述/…）复用全局词典既有词条，
     *     此处只注册本模块新增的键。）
     * ===================================================================== */
    RFLang.register([
      Object.assign(["质性研究", "Qualitative research", "Recherche qualitative", "Качественное исследование", "Investigación cualitativa", "質的研究", "Pesquisa qualitativa", "Qualitative Forschung", "Квалитативно истраживање", "질적 연구"], { aliases: ["sd.label.qualitative"] }),
      Object.assign(["混合方法", "Mixed methods", "Méthodes mixtes", "Смешанные методы", "Métodos mixtos", "混合研究法", "Métodos mistos", "Mixed Methods", "Мешовите методе", "혼합 방법"] , { aliases: ["sd.label.mixed_methods"] }),
      Object.assign(["调查研究", "Survey research", "Enquête", "Опросное исследование", "Estudio de encuesta", "調査研究", "Pesquisa por inquérito", "Befragungsstudie", "Анкетно истраживање", "설문 연구"], { aliases: ["sd.label.survey"] }),
      Object.assign(["话语/语料分析", "Discourse / corpus analysis", "Analyse du discours / de corpus", "Дискурс-анализ / корпусный анализ", "Análisis del discurso / de corpus", "談話・コーパス分析", "Análise do discurso / de corpus", "Diskurs- / Korpusanalyse", "Анализа дискурса / корпуса", "담화·말뭉치 분석"], { aliases: ["sd.label.discourse"] }),
      ["未识别", "Unrecognized", "Non identifié", "Не распознано", "No identificado", "未分類", "Não identificado", "Nicht erkannt", "Није препознато", "미식별"],
      ["本地关键词规则判定为「{0}」（命中：{1}）。仅供参考，请人工核对。", "Classified as “{0}” by local keyword rules (matched: {1}). Advisory only — verify manually.", "Classifié comme « {0} » par les règles locales de mots-clés (correspondance : {1}). À titre indicatif — vérifiez manuellement.", "Классифицировано как «{0}» локальными правилами по ключевым словам (совпадение: {1}). Только рекомендация — проверьте вручную.", "Clasificado como «{0}» por reglas locales de palabras clave (coincidencia: {1}). Solo orientativo: verifique manualmente.", "ローカルのキーワード規則により「{0}」に分類（一致：{1}）。参考情報のため手動で確認してください。", "Classificado como “{0}” por regras locais de palavras-chave (correspondência: {1}). Apenas orientativo — verifique manualmente.", "Durch lokale Schlagwortregeln als „{0}“ klassifiziert (Treffer: {1}). Nur orientierend – bitte manuell prüfen.", "Класификовано као „{0}“ локалним правилима кључних речи (поклапање: {1}). Само оријентационо — проверите ручно.", "로컬 키워드 규칙으로 「{0}」(으)로 분류됨(일치: {1}). 참고용이니 수동으로 확인하세요."],
      ["未识别到明确的研究设计关键词（规则精确优先，宁缺毋滥）。", "No explicit study-design keyword was recognized (precision-first rules; nothing is guessed).", "Aucun mot-clé explicite de design d’étude reconnu (règles précises d’abord ; aucune supposition).", "Явное ключевое слово дизайна исследования не распознано (правила точности в приоритете; догадки исключены).", "No se reconoció ninguna palabra clave explícita de diseño del estudio (reglas de precisión primero; sin conjeturas).", "明確な研究デザインのキーワードは認識されませんでした（精確優先の規則。推測はしません）。", "Nenhuma palavra-chave explícita de desenho do estudo reconhecida (regras de precisão primeiro; sem suposições).", "Es wurde kein eindeutiges Schlagwort zum Studiendesign erkannt (Präzision vor Vermutung).", "Није препозната експлицитна кључна реч дизајна студије (правила прецизности прво; без нагађања).", "명시적인 연구 설계 키워드가 인식되지 않았습니다(정밀도 우선 규칙, 추측 없음)."],
      ["本地规则已预选排除理由（判定非随机对照试验），请人工核对后保存。", "The local rule prefilled the exclusion reason (judged not a randomized controlled trial); verify manually before saving.", "La règle locale a présélectionné le motif d’exclusion (jugé non essai contrôlé randomisé) ; vérifiez manuellement avant d’enregistrer.", "Локальное правило предварительно выбрало причину исключения (признано не рандомизированным контролируемым исследованием); проверьте вручную перед сохранением.", "La regla local preseleccionó el motivo de exclusión (juzgado no ensayo controlado aleatorizado); verifique manualmente antes de guardar.", "ローカル規則が除外理由を事前選択しました（無作為化比較試験ではないと判定）。保存前に手動で確認してください。", "A regra local pré-selecionou o motivo de exclusão (julgado não ensaio randomizado); verifique manualmente antes de salvar.", "Die lokale Regel hat den Ausschlussgrund vorausgewählt (als nicht-randomisierte kontrollierte Studie eingestuft); bitte vor dem Speichern manuell prüfen.", "Локално правило је унапред изабрало разлог искључења (оцењено као не-рандомизована контролисана студија); проверите ручно пре чувања.", "로컬 규칙이 제외 사유를 미리 선택했습니다(무작위 대조시험이 아닌 것으로 판단). 저장 전에 수동으로 확인하세요."],
      ["研究设计（本地规则）", "Study design (local rules)", "Design de l’étude (règles locales)", "Дизайн исследования (локальные правила)", "Diseño del estudio (reglas locales)", "研究デザイン（ローカル規則）", "Desenho do estudo (regras locais)", "Studiendesign (lokale Regeln)", "Дизајн студије (локална правила)", "연구 설계(로컬 규칙)"],
    ]);

    /* =====================================================================
     * 二、样式（徽标族与 ai_suggest .rf-ai-badge 同规格：.72rem 圆角胶囊）
     * ===================================================================== */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-sd-badge{display:inline-flex;align-items:center;font-size:.72rem;border-radius:999px;padding:2px 10px;border:1px solid var(--border);color:var(--muted);margin:4px 0 2px;max-width:100%;cursor:help}',
      '.rf-sd-badge.rf-sd-rct{color:#2F6F6A;border-color:currentColor;font-weight:600}',
      '.rf-sd-badge.rf-sd-syn{color:var(--primary);border-color:currentColor;font-weight:600}',
      '.rf-sd-badge.rf-sd-qualitative{background:#EDE7FB;color:#5B3FB5}',
      '.rf-sd-badge.rf-sd-mixed_methods{background:#FDEEDC;color:#8A4B08}',
      '.rf-sd-badge.rf-sd-survey{background:#DFF3F7;color:#0E6478}',
      '.rf-sd-badge.rf-sd-discourse{background:#F9E4F1;color:#8C2A66}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* =====================================================================
     * 三、状态与工具
     * ===================================================================== */
    /* 标签稳定代码 → 显示名（复用全局词典既有词条，evidence_mapper.js 同族）。 */
    const LABELS = {
      meta_analysis: 'Meta 分析', systematic_review: '系统综述', rct: '随机对照试验',
      cohort: '队列研究', case_control: '病例对照', cross_sectional: '横断面研究',
      diagnostic: '诊断准确性', other: '未识别',
      qualitative: 'sd.label.qualitative', mixed_methods: 'sd.label.mixed_methods',
      survey: 'sd.label.survey', discourse: 'sd.label.discourse',
    };
    const SYN_LABELS = { meta_analysis: true, systematic_review: true };

    const state = {
      scope: null,          // 已缓存数据的作用域（task|user|screener）
      items: null,          // {zotero_key: 分类结果}；null=不可用/未拉取
      unavailable: false,   // 拉取失败（本作用域内不再重试，避免重试风暴）
      flight: null,         // 进行中的请求
      flightScope: null,
      userTouched: false,   // 用户改动过当前卡片的理由下拉（则不打扰）
      applying: false,      // 自己派发的 change 事件进行中（不算用户操作）
    };

    function safe(fn) { return function () { try { return fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }
    const scopeKey = () => [S.task?.task_id || '', S.user?.user_id ?? '', S.screener || ''].join('|');
    const classificationFor = key => {
      const item = state.items && state.items[key];
      return item && typeof item === 'object' && typeof item.label === 'string' ? item : null;
    };

    /* =====================================================================
     * 四、数据拉取（每作用域一次；失败=不可用，静默降级）
     * ===================================================================== */
    function ensureData() {
      const scope = scopeKey();
      if (state.scope === scope && (state.items || state.unavailable)) return;   // 已就绪：由 badge() 直接决定是否渲染
      if (!S.task || typeof taskApi !== 'function') return;
      if (state.flight && state.flightScope === scope) return;
      if (state.scope !== scope) { state.scope = scope; state.items = null; state.unavailable = false; }
      state.flightScope = scope;
      state.flight = (async () => {
        try {
          const d = await api(taskApi() + '/study-designs' + scrQ());
          if (scopeKey() !== scope) return;                    // 作用域已变：丢弃
          /* truncated=true：后端命中条目上限、只回了部分目录（其注释承诺
             「绝不静默丢弃」）。前端把「部分结果」整体视作不可用——宁可没有
             徽标，也不让未列出的文献看起来像「未识别」；与 available=false
             同一降级路径。 */
          if (!d || d.available === false || d.truncated === true ||
              !d.items || typeof d.items !== 'object') {
            state.items = null; state.unavailable = true;
          } else {
            state.items = d.items; state.unavailable = false;
          }
          paint();
          applyPendingPrefill();
          scheduleRepaint();
        } catch (e) {
          if (scopeKey() !== scope) return;
          state.items = null; state.unavailable = true;        // 失败优雅降级：无徽标、无预填
          paint();
          scheduleRepaint();
        } finally {
          state.flight = null;
        }
      })();
    }

    /* =====================================================================
     * 五、徽标渲染（textContent/setAttribute，绝不 innerHTML）
     * ===================================================================== */
    function renderBadge(node, item) {
      const label = Object.hasOwn(LABELS, item.label) ? item.label : 'other';
      node.className = 'rf-sd-badge rf-sd-' + label + (SYN_LABELS[label] ? ' rf-sd-syn' : '');
      node.textContent = T(LABELS[label]);
      node.setAttribute('title', item.recognized
        ? T('本地关键词规则判定为「{0}」（命中：{1}）。仅供参考，请人工核对。', T(LABELS[label]), item.matched_keyword || '—')
        : T('未识别到明确的研究设计关键词（规则精确优先，宁缺毋滥）。'));
    }

    /* 只处理**已入树**节点：badge() 在 body.append(...) 的实参求值阶段调用，
       此刻节点尚未挂载；对未挂载节点做 removeChild 无意义、渲染也会被后续
       占位逻辑覆盖。已入树节点在数据到达时由本次 paint 补全或移除。 */
    function paint() {
      for (const node of document.querySelectorAll('[data-rf-sd-badge]')) {
        if (node.isConnected === false) continue;
        const item = classificationFor(node.getAttribute('data-rf-sd-badge'));
        if (!item) { if (node.parentNode) node.parentNode.removeChild(node); continue; }
        renderBadge(node, item);
      }
    }

    /* 数据到达后的下一帧再 paint 一次：覆盖「badge() 先求值、节点后入树」
       的顺序（浏览器里 append 是同步挂载，但仍可能在 paint 之后才完成
       append 的调用栈）；占位节点若在数据到达前新建，也靠这一帧决定去留。 */
    function scheduleRepaint() {
      const raf = typeof window.requestAnimationFrame === 'function'
        ? cb => window.requestAnimationFrame(cb)
        : cb => setTimeout(cb, 0);
      try { raf(() => { try { paint(); } catch (e) { /* 单点故障 */ } }); } catch (e) { /* 单点故障 */ }
    }

    /* renderScreen 卡片锚点（AL 分数条旁）。
       - 数据已就绪（成功或失败）且该键无分类 → 返回 null（调用方 filter 剔除；
         绝不留下永不消失的「…」占位）；
       - 数据仍在途中 → 占位「…」，数据到达后 paint 补全；
       - 数据已就绪且命中 → 直接渲染终态。 */
    function badge(article) {
      const key = article && article.zotero_key ? String(article.zotero_key) : '';
      const canQuery = !!(S.task && typeof taskApi === 'function');
      if (canQuery) ensureData();
      const item = classificationFor(key);
      if (item) {
        const node = el('span', { 'data-rf-sd-badge': key, 'aria-label': T('研究设计（本地规则）') });
        renderBadge(node, item);
        return node;
      }
      /* 无任务（未登录/未选任务）或数据已就绪但无此键 → 不渲染任何占位 */
      const ready = !canQuery || !!(state.items || state.unavailable);
      if (ready) return null;
      const node = el('span', { 'data-rf-sd-badge': key, 'aria-label': T('研究设计（本地规则）') });
      node.className = 'rf-sd-badge';
      node.textContent = '…';
      return node;
    }

    /* =====================================================================
     * 六、预填不代存（判非 RCT → 预选「研究设计不符」；采纳＝填控件先例）
     * ===================================================================== */
    function prefill(selectEl, article) {
      state.userTouched = false;   // 每次渲染拿到的是新控件：重置「用户改过」标记
      if (!selectEl || selectEl.value !== '') return false;
      const item = classificationFor(article && article.zotero_key);
      if (!item || !item.recognized || item.is_rct || !item.suggest_exclusion_reason) return false;
      state.applying = true;
      try {
        selectEl.value = item.suggest_exclusion_reason;   // 与下拉选项值同串（研究设计不符）
        if (typeof selectEl.dispatchEvent === 'function')
          selectEl.dispatchEvent(new Event('change', { bubbles: true }));
        if (typeof selectEl.setAttribute === 'function')
          selectEl.setAttribute('title', T('本地规则已预选排除理由（判定非随机对照试验），请人工核对后保存。'));
      } finally { state.applying = false; }
      return true;
    }

    /* 数据晚于首屏到达时的补填：只动当前初筛卡且用户未改过；
       我们自己的改动随即重置「干净」基线，不误报未保存草稿。 */
    function applyPendingPrefill() {
      if (state.userTouched) return;
      const select = document.getElementById('excReason');
      const article = Array.isArray(S.articles) ? S.articles[S.scrIdx] : null;
      if (!select || !article || select.value !== '') return;
      if (prefill(select, article) && window.WorkflowControls &&
          typeof window.WorkflowControls.markDecisionClean === 'function')
        window.WorkflowControls.markDecisionClean('screen');
    }

    /* 用户亲手改过理由下拉 → 本卡片内不再自动预填/补填 */
    document.addEventListener('change', event => {
      const target = event.target;
      if (state.applying || !target || target.id !== 'excReason') return;
      state.userTouched = true;
    });

    /* ---- 缓存失效（复审第 3 轮 issue 3）：同作用域内新导入并激活的文献，
       此前拿不到徽标且无任何提示（只有切任务/重开应用才恢复）。导入激活会
       派发 reviewflow:import-completed（unified_import_workspace.js:114/123）；
       工作流/目录变更走 reviewflow:workflow-changed（index.html:5580 由任务
       事件流转发）。两者都只**丢弃缓存**、不立即拉取——下一次卡片渲染
       （badge()）再按「每作用域一次」重新取数，因此不引入轮询、不产生后台
       请求；若当时正停在初筛页，则顺手重绘一次以让新徽标及时出现。 */
    function invalidate(reason) {
      state.scope = null;
      state.items = null;
      state.unavailable = false;
      state.flight = null;
      state.flightScope = null;
      if (S.page !== 'screen') return;              // 不在初筛页：等下次渲染自然重取
      paint();                                       // 先清掉旧徽标（未知键节点被移除）
      if (Array.isArray(S.articles) && S.articles.length &&
          typeof renderScreen === 'function') {
        try { if (!window.WorkflowControls?.decisionDirty()) renderScreen(); } catch (e) { /* 单点故障 */ }
      }
    }
    for (const name of ['reviewflow:import-completed', 'reviewflow:workflow-changed'])
      document.addEventListener(name, safe(() => invalidate(name)));

    /* 语言切换：重绘徽标（预填 title 不追改，避免覆盖用户可能的手动选择） */
    document.addEventListener('reviewflow:language-changed', safe(() => paint()));

    window.StudyDesignBadge = Object.freeze({ badge, prefill });
  } catch (e) { /* 模块整体故障：应用其余部分不受影响 */ }
})();
