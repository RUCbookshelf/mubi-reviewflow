/* Preview presentation only. Keep every control, handler, ownership boundary and
 * draft in the original product node. Mobile composition belongs to the product. */
(function () {
  'use strict';
  const home = document.querySelector('section.page[data-p="home"]');
  if (!home) return;
  const desktop = matchMedia('(min-width:761px)');
  const moves = new Map(), added = new Set(), classes = new Map();
  let frame = 0, observing = false, modelPage = null;
  const q = (s, root = home) => root?.querySelector(s);
  const qa = (s, root = home) => [...(root?.querySelectorAll(s) || [])];
  const languages = ['en', 'fr', 'ru', 'es', 'ja', 'pt', 'de', 'sr', 'ko'];
  const keyStorageNotice = '保存位置：桌面版将 API Key 保存在本机的数据目录；服务器版保存在部署服务器的数据目录。密钥文件目前未加密；应用会尽可能限制文件的访问权限。API Key 不会持久保存到浏览器，也不会写入任务目录或导出包。';
  // Preview-owned labels stay local; the product translation registry is untouched.
  const translations = {
    [keyStorageNotice]: ['Storage location: the desktop app saves API keys in the data directory on your computer; the server version saves them in the data directory on the server hosting ReviewFlow. Key files are currently unencrypted; the app restricts file access permissions where possible. API keys are not persistently stored in the browser or included in task directories or export packages.', 'Emplacement : l’application de bureau enregistre les clés API dans le répertoire de données de votre ordinateur ; la version serveur les enregistre dans celui du serveur hébergeant ReviewFlow. Les fichiers de clés ne sont actuellement pas chiffrés ; l’application limite autant que possible leurs droits d’accès. Les clés API ne sont pas conservées durablement dans le navigateur, ni incluses dans les dossiers de tâches ou les exports.', 'Место хранения: настольное приложение сохраняет ключи API в каталоге данных вашего компьютера, а серверная версия — в каталоге данных сервера, на котором работает ReviewFlow. Файлы ключей сейчас не зашифрованы; приложение по возможности ограничивает права доступа к ним. Ключи API не сохраняются постоянно в браузере и не включаются в папки задач или экспортные пакеты.', 'Ubicación: la aplicación de escritorio guarda las claves API en el directorio de datos de su equipo; la versión de servidor las guarda en el directorio de datos del servidor que aloja ReviewFlow. Los archivos de claves no están cifrados actualmente; la aplicación restringe sus permisos de acceso cuando es posible. Las claves API no se guardan de forma persistente en el navegador ni se incluyen en carpetas de tareas o paquetes exportados.', '保存先：デスクトップ版では、このコンピューターのデータディレクトリに API キーを保存します。サーバー版では、ReviewFlow を実行しているサーバーのデータディレクトリに保存します。キーのファイルは現在暗号化されていません。アプリは可能な範囲でファイルへのアクセス権を制限します。API キーはブラウザーに永続保存されず、タスクのディレクトリやエクスポートにも含まれません。', 'Local de armazenamento: o aplicativo desktop salva as chaves de API no diretório de dados do seu computador; a versão de servidor as salva no diretório de dados do servidor que hospeda o ReviewFlow. Os arquivos de chaves não são criptografados atualmente; o aplicativo restringe as permissões de acesso sempre que possível. As chaves de API não são armazenadas permanentemente no navegador nem incluídas nas pastas de tarefas ou nos pacotes de exportação.', 'Speicherort: Die Desktop-App speichert API-Schlüssel im Datenverzeichnis Ihres Computers; die Serverversion speichert sie im Datenverzeichnis des Servers, auf dem ReviewFlow läuft. Schlüsseldateien sind derzeit unverschlüsselt; die App schränkt die Dateizugriffsrechte nach Möglichkeit ein. API-Schlüssel werden weder dauerhaft im Browser gespeichert noch in Aufgabenordner oder Exportpakete aufgenommen.', 'Место чувања: десктоп апликација чува API кључеве у директоријуму података на вашем рачунару; серверска верзија их чува у директоријуму података сервера на коме ради ReviewFlow. Датотеке са кључевима тренутно нису шифроване; апликација ограничава права приступа кад год је могуће. API кључеви се не чувају трајно у прегледачу нити се укључују у фасцикле задатака или извозне пакете.', '저장 위치: 데스크톱 앱은 현재 컴퓨터의 데이터 디렉터리에 API 키를 저장하며, 서버 버전은 ReviewFlow가 배포된 서버의 데이터 디렉터리에 저장합니다. 키 파일은 현재 암호화되지 않으며 앱은 가능한 범위에서 파일 접근 권한을 제한합니다. API 키는 브라우저에 영구 저장되지 않으며 작업 디렉터리나 내보내기 패키지에도 포함되지 않습니다.'],
    '从当前任务继续，按研究流程进入工作区。': ['Continue your current task and open a workspace for each research stage.', 'Reprenez la tâche actuelle et ouvrez un espace pour chaque étape de recherche.', 'Продолжите текущую задачу и откройте рабочую область нужного этапа исследования.', 'Continúe la tarea actual y abra un espacio para cada etapa de investigación.', '現在のタスクを続け、研究の各段階のワークスペースを開きます。', 'Continue a tarefa atual e abra um espaço para cada etapa da pesquisa.', 'Setzen Sie die aktuelle Aufgabe fort und öffnen Sie den Arbeitsbereich der jeweiligen Forschungsphase.', 'Наставите текући задатак и отворите радни простор за сваку фазу истраживања.', '현재 작업을 이어서 연구 단계별 작업 공간을 여세요.'],
    '管理任务、成员与各阶段可使用的文献来源。': ['Manage tasks, members and the literature sources available at each stage.', 'Gérez les tâches, les membres et les sources disponibles à chaque étape.', 'Управляйте задачами, участниками и источниками литературы для каждого этапа.', 'Gestione tareas, miembros y fuentes bibliográficas disponibles en cada etapa.', 'タスク、メンバー、各段階で利用できる文献ソースを管理します。', 'Gerencie tarefas, membros e fontes de literatura disponíveis em cada etapa.', 'Verwalten Sie Aufgaben, Mitglieder und die verfügbaren Literaturquellen je Phase.', 'Управљајте задацима, члановима и изворима литературе доступним у свакој фази.', '작업, 구성원 및 단계별 사용 가능한 문헌 출처를 관리하세요.'],
    '分别管理初筛与复筛的常用排除理由。': ['Manage reusable exclusion reasons separately for initial and full-text screening.', 'Gérez séparément les motifs réutilisables d’exclusion pour la sélection initiale et en texte intégral.', 'Управляйте типовыми причинами исключения отдельно для первичного и полнотекстового отбора.', 'Gestione por separado los motivos de exclusión reutilizables para el cribado inicial y de texto completo.', '一次スクリーニングと全文スクリーニングの除外理由を個別に管理します。', 'Gerencie separadamente os motivos de exclusão reutilizáveis para a triagem inicial e de texto completo.', 'Verwalten Sie wiederverwendbare Ausschlussgründe für Erst- und Volltextscreening getrennt.', 'Одвојено управљајте уобичајеним разлозима искључења за почетни и преглед пуног текста.', '초기 선별과 원문 선별에 사용할 제외 사유를 각각 관리하세요.'],
    '设置文献排序策略、更新间隔与阅读偏好。': ['Configure literature ranking, refresh intervals and reading preferences.', 'Configurez le classement, la fréquence de mise à jour et les préférences de lecture.', 'Настройте ранжирование литературы, интервалы обновления и параметры чтения.', 'Configure la clasificación de referencias, los intervalos de actualización y las preferencias de lectura.', '文献の並べ替え、更新間隔、閲覧設定を調整します。', 'Configure a ordenação da literatura, os intervalos de atualização e as preferências de leitura.', 'Konfigurieren Sie Literaturreihenfolge, Aktualisierungsintervalle und Lesepräferenzen.', 'Подесите рангирање литературе, интервале освежавања и поставке читања.', '문헌 정렬, 갱신 간격 및 읽기 환경설정을 조정하세요.'],
    '模型设置': ['Model settings', 'Paramètres des modèles', 'Настройки моделей', 'Configuración de modelos', 'モデル設定', 'Configurações dos modelos', 'Modelleinstellungen', 'Подешавања модела', '모델 설정'],
    '配置通用模型与决策模型的个人 API Key。': ['Configure personal API keys for general and decision models.', 'Configurez les clés API personnelles des modèles généraux et de décision.', 'Настройте личные ключи API для универсальных моделей и моделей принятия решений.', 'Configure las claves API personales para los modelos generales y de decisión.', '汎用モデルと判定モデルの個人 API キーを設定します。', 'Configure as chaves de API pessoais dos modelos gerais e de decisão.', 'Konfigurieren Sie persönliche API-Schlüssel für allgemeine Modelle und Entscheidungsmodelle.', 'Подесите личне API кључеве за опште моделе и моделе за одлучивање.', '범용 모델 및 의사결정 모델의 개인 API 키를 설정하세요.'],
    '明确哪些维度需要证据，并配置编码辅助与综合方法。': ['Specify which dimensions require evidence, and configure coding assistance and synthesis methods.', 'Précisez les dimensions nécessitant des preuves et configurez l’aide au codage et les méthodes de synthèse.', 'Укажите параметры, требующие подтверждения, и настройте помощь при кодировании и методы синтеза.', 'Especifique qué dimensiones requieren evidencia y configure la ayuda de codificación y los métodos de síntesis.', '証拠が必要な次元を指定し、コーディング支援と統合方法を設定します。', 'Especifique quais dimensões exigem evidências e configure a assistência à codificação e os métodos de síntese.', 'Legen Sie fest, welche Dimensionen Belege benötigen, und konfigurieren Sie Kodierhilfen und Synthesemethoden.', 'Одредите које димензије захтевају доказе и подесите помоћ при кодирању и методе синтезе.', '근거가 필요한 차원을 지정하고 코딩 지원 및 종합 방법을 설정하세요.'],
    '调整语言、主题与界面密度，查看当前的数据存放设置。': ['Adjust language, theme and interface density, and review data storage settings.', 'Réglez la langue, le thème et la densité de l’interface, puis consultez le stockage des données.', 'Настройте язык, тему и плотность интерфейса и проверьте параметры хранения данных.', 'Ajuste el idioma, el tema y la densidad de la interfaz y consulte la configuración de almacenamiento.', '言語、テーマ、表示密度を調整し、データの保存設定を確認します。', 'Ajuste o idioma, o tema e a densidade da interface e consulte as configurações de armazenamento.', 'Passen Sie Sprache, Design und Oberflächendichte an und prüfen Sie die Datenspeicherung.', 'Подесите језик, тему и густину интерфејса и прегледајте поставке чувања података.', '언어, 테마, 화면 밀도를 조정하고 데이터 저장 설정을 확인하세요.'],
    '文献与筛选': ['Literature & screening', 'Littérature et sélection', 'Литература и отбор', 'Bibliografía y cribado', '文献とスクリーニング', 'Literatura e triagem', 'Literatur und Screening', 'Литература и одабир', '문헌 및 선별'],
    '证据与分析': ['Evidence & analysis', 'Preuves et analyse', 'Доказательства и анализ', 'Evidencia y análisis', '証拠と分析', 'Evidências e análise', 'Belege und Analyse', 'Докази и анализа', '근거 및 분석'],
    '协作与产出': ['Collaboration & outputs', 'Collaboration et résultats', 'Совместная работа и результаты', 'Colaboración y resultados', '共同作業と成果', 'Colaboração e resultados', 'Zusammenarbeit und Ergebnisse', 'Сарадња и резултати', '협업 및 결과물'],
    '任务列表': ['Task list', 'Liste des tâches', 'Список задач', 'Lista de tareas', 'タスク一覧', 'Lista de tarefas', 'Aufgabenliste', 'Листа задатака', '작업 목록'],
    '阅读偏好': ['Reading preferences', 'Préférences de lecture', 'Параметры чтения', 'Preferencias de lectura', '閲覧設定', 'Preferências de leitura', 'Lesepräferenzen', 'Поставке читања', '읽기 환경설정'],
    '显示偏好': ['Display preferences', 'Préférences d’affichage', 'Параметры отображения', 'Preferencias de visualización', '表示設定', 'Preferências de exibição', 'Anzeigepräferenzen', 'Поставке приказа', '표시 환경설정']
  };
  const tr = text => {
    const language = typeof RFLang !== 'undefined' ? RFLang.get() : 'zh-CN';
    const index = languages.indexOf(language);
    return translations[text] && index >= 0 ? translations[text][index] : typeof T === 'function' ? T(text) : text;
  };
  function create(tag, cls, text) {
    const node = document.createElement(tag);
    node.className = cls;
    if (text) { node.textContent = tr(text); node.dataset.rfhLabel = text; }
    added.add(node);
    return node;
  }
  function mark(node, cls) {
    if (!node || node.classList.contains(cls)) return;
    if (!classes.has(node)) classes.set(node, new Set());
    classes.get(node).add(cls);
    node.classList.add(cls);
  }
  function move(node, target, before = null) {
    if (!node || !target || node.parentNode === target && (!before || node.nextSibling === before)) return;
    if (!moves.has(node)) {
      const anchor = document.createComment('home preview position');
      node.before(anchor); moves.set(node, anchor);
    }
    target.insertBefore(node, before);
  }
  function restore() {
    for (const [node, anchor] of [...moves].reverse()) {
      if (anchor.isConnected) {
        // A product rerender owns node deletion. Never resurrect a stale control.
        if (node.isConnected) anchor.replaceWith(node); else anchor.remove();
      }
    }
    moves.clear();
    for (const [node, values] of classes) for (const cls of values) node.classList.remove(cls);
    classes.clear();
    for (const node of added) node.remove();
    added.clear();
  }
  function syncModelPage() {
    if (modelPage) modelPage.panel.hidden = modelPage.tab.getAttribute('aria-selected') !== 'true';
  }
  function updateModelDisclosure() {
    if (!modelPage) return;
    const notice = q(':scope > p.rf-ai-hint:last-child', modelPage.keyCard);
    if (!notice) return;
    if (modelPage.disclosure?.node !== notice) {
      // renderKeyCard owns language/account rerenders. Journal only the current
      // footer's text nodes and never inspect a key field or recreate a control.
      modelPage.disclosure = {node: notice, children: [...notice.childNodes]};
    }
    const text = tr(keyStorageNotice);
    if (notice.textContent !== text) notice.textContent = text;
  }
  function mountModelPage() {
    if (modelPage) { syncModelPage(); updateModelDisclosure(); return; }
    const tabs = q('.wf-tabs'), ml = q('#wf-panel-ml'), mlTab = q('#wf-tab-ml'), keyCard = q('#rfAiKeyCard');
    if (!tabs || !ml || !mlTab || !keyCard || !window.WorkflowControls?.homeTab) return;
    // The product activate(key) already selects real tabs and measures its shared
    // indicator. Only the seventh panel and the expanded keyboard cycle need an
    // adapter; the six existing handlers and exported homeTab stay unchanged.
    const tab = document.createElement('button');
    tab.type = 'button'; tab.className = mlTab.className; tab.id = 'wf-tab-models';
    tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'wf-panel-models');
    tab.setAttribute('aria-selected', 'false'); tab.tabIndex = -1;
    tab.dataset.rfhLabel = '模型设置'; tab.textContent = tr('模型设置');
    const panel = document.createElement('div');
    panel.id = 'wf-panel-models'; panel.className = 'wf-panel'; panel.hidden = true;
    panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id);
    const header = document.createElement('header'); header.className = 'rfh-panel-heading';
    for (const [tag, text] of [['h2', '模型设置'], ['p', '配置通用模型与决策模型的个人 API Key。']]) {
      const label = document.createElement(tag); label.dataset.rfhLabel = text; label.textContent = tr(text);
      if (tag === 'p') label.className = 'hint'; header.append(label);
    }
    panel.append(header); ml.after(panel); mlTab.after(tab);
    const anchor = document.createComment('home model settings position'); keyCard.before(anchor); panel.append(keyCard);
    const selectModel = () => { window.WorkflowControls.homeTab('models'); syncModelPage(); };
    const click = event => { if (event.target.closest('[role="tab"]')) syncModelPage(); };
    const keyboard = event => {
      const target = event.target.closest('[role="tab"]');
      if (!target || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const list = [...tabs.querySelectorAll('[role="tab"]')], index = list.indexOf(target);
      if (index < 0) return;
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? list.length - 1 :
        (index + (event.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length;
      event.preventDefault(); event.stopImmediatePropagation(); list[next].click(); list[next].focus();
    };
    const selection = new MutationObserver(syncModelPage);
    selection.observe(tabs, {attributes: true, subtree: true, attributeFilter: ['aria-selected']});
    const resize = new ResizeObserver(() => {
      if (tab.getAttribute('aria-selected') !== 'true' || !tab.getClientRects().length) return;
      const scroller = document.querySelector('#appMain .view'), scroll = scroller?.scrollTop;
      window.WorkflowControls.homeTab('models'); syncModelPage();
      if (scroller) scroller.scrollTop = scroll;
    });
    tab.addEventListener('click', selectModel); tabs.addEventListener('click', click);
    tabs.addEventListener('keydown', keyboard, true); resize.observe(tab);
    modelPage = {tab, panel, keyCard, anchor, tabs, selection, resize, click, keyboard, selectModel};
    updateModelDisclosure();
  }
  function unmountModelPage() {
    if (!modelPage) return;
    const state = modelPage;
    if (state.tab.getAttribute('aria-selected') === 'true') window.WorkflowControls.homeTab('ml');
    state.selection.disconnect(); state.resize.disconnect();
    state.tabs.removeEventListener('click', state.click); state.tabs.removeEventListener('keydown', state.keyboard, true);
    state.tab.removeEventListener('click', state.selectModel);
    if (state.disclosure?.node.isConnected) state.disclosure.node.replaceChildren(...state.disclosure.children);
    if (state.anchor.isConnected) {
      if (state.keyCard.isConnected) state.anchor.replaceWith(state.keyCard); else state.anchor.remove();
    }
    state.tab.remove(); state.panel.remove(); modelPage = null;
  }
  function card(parent, cls, title, before = null) {
    const node = create('section', 'rfh-card ' + cls);
    if (title) node.append(create('h3', 'rfh-card-title', title));
    parent.insertBefore(node, before);
    return node;
  }
  function heading(panel, title, description) {
    if (!panel || q(':scope > .rfh-panel-heading', panel)) return;
    const header = create('header', 'rfh-panel-heading');
    header.append(create('h2', '', title), create('p', 'hint', description));
    panel.prepend(header);
  }
  function pair(panel, cls, title) {
    let box = q(':scope > .' + cls, panel);
    const loose = qa(':scope > label, :scope > select', panel);
    if (!loose.length) return box;
    if (!box) box = card(panel, cls, title, loose[0]);
    for (const node of loose) move(node, box);
    return box;
  }
  function overview(panel) {
    heading(panel, '概览', '从当前任务继续，按研究流程进入工作区。');
    const steps = q('.steps', panel);
    if (!steps || q('.rfh-feature-group', steps)) return;
    const entries = qa(':scope > .step', steps);
    if (entries.length !== 12) return;
    mark(steps, 'rfh-features');
    if (steps.previousElementSibling?.tagName === 'H3') mark(steps.previousElementSibling, 'rfh-repeated-heading');
    for (const [title, indices] of [
      ['文献与筛选', [0, 2, 3, 1]],
      ['证据与分析', [4, 8, 7, 5]],
      ['协作与产出', [10, 9, 6, 11]]
    ]) {
      const group = card(steps, 'rfh-feature-group', title);
      for (const index of indices) move(entries[index], group);
    }
  }
  function tasks(panel) {
    heading(panel, '任务与团队', '管理任务、成员与各阶段可使用的文献来源。');
    const createCard = q('#newTaskName', panel)?.closest('.card');
    if (createCard && !createCard.classList.contains('rfh-task-create')) {
      mark(createCard, 'rfh-task-create');
      const title = createCard.previousElementSibling;
      if (title?.tagName === 'H3') move(title, createCard, createCard.firstChild);
    }
    const grid = q('#hubGrid', panel);
    if (grid && !q('.rfh-task-list', panel)) {
      const between = [];
      for (let node = grid.previousElementSibling; node && !node.matches('.card, h3, header'); node = node.previousElementSibling) between.unshift(node);
      const list = card(panel, 'rfh-task-list', '任务列表', between[0] || grid);
      for (const node of between) move(node, list);
      move(grid, list);
    }
    const team = q('#wf-team-management', panel);
    mark(team, 'rfh-team');
    const search = q('#rfSearchStrategy', team), teamTitle = q('#wf-team-title', team);
    if (search && teamTitle && teamTitle.nextElementSibling !== search) move(search, team, teamTitle.nextSibling);
  }
  function reasons(panel) {
    heading(panel, '筛选策略设置', '分别管理初筛与复筛的常用排除理由。');
    const host = q('#wf-reasons', panel);
    if (!host) return;
    for (const title of qa(':scope > h4', host)) {
      const nodes = [title];
      for (let next = title.nextElementSibling; next && next.tagName !== 'H4'; next = next.nextElementSibling) nodes.push(next);
      const group = create('section', 'rfh-reason-stage');
      host.insertBefore(group, title);
      for (const node of nodes) move(node, group);
      const input = q(':scope > input', group);
      if (input && input.nextElementSibling?.tagName === 'BUTTON') {
        const button = input.nextElementSibling, row = create('div', 'rfh-reason-add');
        group.insertBefore(row, input); move(input, row); move(button, row);
      }
    }
  }
  function machineLearning(panel) {
    heading(panel, '机器学习', '设置文献排序策略、更新间隔与阅读偏好。');
    const ranking = q('#alStrategy', panel)?.closest('.card');
    if (ranking && !ranking.classList.contains('rfh-ranking')) {
      mark(ranking, 'rfh-ranking');
      const title = ranking.previousElementSibling;
      if (title?.tagName === 'H3') move(title, ranking, ranking.firstChild);
    }
    const preferences = pair(panel, 'rfh-reading-prefs', '阅读偏好');
    const blind = q('.al-blind-row', panel);
    if (preferences && blind && !preferences.contains(blind)) move(blind, preferences);
    if (ranking && preferences && !q(':scope > .rfh-ml-top', panel)) {
      const top = create('div', 'rfh-ml-top'); panel.insertBefore(top, ranking);
      move(ranking, top); move(preferences, top);
    }
  }
  function groupAISections(host) {
    if (!host) return;
    for (const title of qa(':scope > h4', host)) {
      const nodes = [title];
      for (let next = title.nextElementSibling; next && next.tagName !== 'H4'; next = next.nextElementSibling) nodes.push(next);
      const group = create('section', 'rfh-ai-section');
      host.insertBefore(group, title);
      for (const node of nodes) move(node, group);
      if (q('.rf-ai-mapping, textarea, .rf-ai-methods', group)) mark(group, 'rfh-ai-wide');
    }
  }
  function coding(panel) {
    heading(panel, '编码证据要求', '明确哪些维度需要证据，并配置编码辅助与综合方法。');
    const preferences = pair(panel, 'rfh-evidence-prefs', '显示偏好');
    const requirements = q('#wf-requirements', panel);
    if (requirements && preferences && !q(':scope > .rfh-coding-top', panel)) {
      const top = create('div', 'rfh-coding-top'); panel.insertBefore(top, preferences);
      move(requirements, top); move(preferences, top);
    }
    groupAISections(q('#rfAiTaskCard', panel));
  }
  function releaseAppearancePreferences() {
    const panel = q('#wf-panel-appearance');
    if (!panel) return [];
    const preferences = q('#densitySel', panel)?.closest('.card');
    if (!preferences) return [];
    const language = q('select:has(option[value="zh-CN"])', panel);
    const nodes = [language, language?.previousElementSibling, ...qa(':scope > h3', preferences)];
    const transferred = [];
    for (const node of nodes) if (node && moves.has(node)) {
      transferred.push({node, anchor: moves.get(node)}); moves.delete(node);
    }
    return transferred;
  }
  function appearance(panel) {
    heading(panel, '外观与存储', '调整语言、主题与界面密度，查看当前的数据存放设置。');
    const preferences = q('#densitySel', panel)?.closest('.card');
    if (preferences && !preferences.hasAttribute('data-rft-preferences-owned') && !preferences.classList.contains('rfh-appearance')) {
      mark(preferences, 'rfh-appearance');
      const title = preferences.previousElementSibling;
      if (title?.tagName === 'H3') move(title, preferences, preferences.firstChild);
      const loose = qa(':scope > label, :scope > select', panel);
      if (loose.length) {
        const language = create('div', 'rfh-language-field');
        const row = q('.row', preferences); row?.prepend(language);
        for (const node of loose) move(node, language);
      }
    }
    const storage = q('#localStorageSettings', panel), title = q('#localStorageTitle', panel);
    // Storage renderer owns its card children; keep the separate original title.
    if (storage) mark(storage, 'rfh-storage');
    if (title) mark(title, 'rfh-storage-title');
  }
  function compose() {
    if (observing) observer.disconnect();
    try {
      if (!document.body.classList.contains('rf-layout-preview-compact')) { restore(); unmountModelPage(); return; }
      mountModelPage();
      if (!desktop.matches) restore();
      for (const [node, anchor] of moves) if (!node.isConnected && !anchor.isConnected) moves.delete(node);
      for (const node of added) if (!node.isConnected) added.delete(node);
      for (const node of classes.keys()) if (!node.isConnected) classes.delete(node);
      const handlers = {overview, tasks, screening: reasons, ml: machineLearning, coding, appearance};
      for (const [key, handler] of desktop.matches ? Object.entries(handlers) : []) {
        const panel = q('#wf-panel-' + key); if (panel) handler(panel);
      }
      for (const node of qa('[data-rfh-label]')) {
        const text = tr(node.dataset.rfhLabel); if (node.textContent !== text) node.textContent = text;
      }
    } finally {
      observer.observe(home, {childList: true, subtree: true}); observing = true;
    }
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(() => { frame = 0; compose(); }); }
  const observer = new MutationObserver(schedule);
  document.addEventListener('reviewflow:layout-preview-mode', compose);
  document.addEventListener('reviewflow:language-changed', schedule);
  desktop.addEventListener('change', () => {
    // Restore before the product mobile composer next runs. On return to desktop,
    // allow its own move journal to settle before wrapping the stable home nodes.
    if (!desktop.matches) { observer.disconnect(); restore(); }
    schedule();
  });
  window.RFHomeLayoutPreview = Object.freeze({refresh: schedule, releaseAppearancePreferences, get ready() { return !!q('#wf-panel-overview'); }});
  compose();
})();
