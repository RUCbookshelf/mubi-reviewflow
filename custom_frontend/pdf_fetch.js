/* ReviewFlow · W2 PDF 自动获取（空白格 B1）前端 —— 文献卡「获取全文」+ 批量入口 + 任务设置卡。
 *
 * 后端：custom_backend/pdf_fetch_api.py（本模块零后端改动）。
 * 治理红线（与后端同源）：
 *   - 默认关：settings.enabled=false 时初筛卡与批量入口零渲染（判定信息一次
 *     拉取，失败静默退出；任务未启用不再发起任何请求）；
 *   - 显式手动触发：所有出站都由用户点击按钮发起，无定时器无自动批量；
 *   - 自包含：IIFE + 十语言词条自包含注册（RFLang.register，中文为源串，
 *     zh/en/fr/ru/es/ja/pt/de/sr/ko）+ 全部动态数据经 textContent（translate:'no'
 *     标注字面量），绝不 innerHTML；
 *   - 失败优雅降级：403/422/502/网络断开按 code 映射为提示文案，任何异常
 *     自捕获，单点故障不影响主应用。
 * 隐私披露：设置卡常显「出站访问范围」行（来自后端 outbound_endpoints，
 * 仅声明端点 + Unpaywall 返回的开放 PDF 链接）。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof RFLang === 'undefined' ||
        typeof T !== 'function' || typeof api !== 'function') return;

    /* =====================================================================
     * 一、十语言词条（模块自包含；列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko）
     * ===================================================================== */
    RFLang.register([
      ["获取全文", "Fetch full text", "Obtenir le texte intégral", "Получить полный текст", "Obtener texto completo", "全文を取得", "Obter texto completo", "Volltext abrufen", "Преузми пун текст", "전문 가져오기"],
      ["获取中…", "Fetching…", "Récupération…", "Получение…", "Obteniendo…", "取得中…", "Buscando…", "Wird abgerufen…", "Преузимање…", "가져오는 중…"],
      ["已获取全文（{0} 页）。", "Full text fetched ({0} pages).", "Texte intégral récupéré ({0} pages).", "Полный текст получен ({0} стр.).", "Texto completo obtenido ({0} páginas).", "全文を取得しました（{0}ページ）。", "Texto completo obtido ({0} páginas).", "Volltext abgerufen ({0} Seiten).", "Пун текст је преузет ({0} страна).", "전문을 가져왔습니다({0}쪽)."],
      ["未找到开放获取的 PDF。", "No open-access PDF found.", "Aucun PDF en accès ouvert trouvé.", "Открытая (OA) версия PDF не найдена.", "No se encontró ningún PDF de acceso abierto.", "オープンアクセスのPDFは見つかりませんでした。", "Nenhum PDF de acesso aberto encontrado.", "Kein Open-Access-PDF gefunden.", "Није пронађен PDF отвореног приступа.", "오픈액세스 PDF를 찾지 못했습니다."],
      ["任务未启用 PDF 自动获取（默认关）。", "PDF auto-fetch is not enabled for this task (off by default).", "La récupération automatique des PDF n’est pas activée pour cette tâche (désactivée par défaut).", "Автоматическая загрузка PDF для этой задачи не включена (по умолчанию выключена).", "La obtención automática de PDF no está activada en esta tarea (desactivada por defecto).", "このタスクではPDF自動取得が有効になっていません（既定は無効）。", "A busca automática de PDF não está ativada nesta tarefa (desativada por padrão).", "Die automatische PDF-Abrufung ist für diese Aufgabe nicht aktiviert (standardmäßig aus).", "Аутоматско преузимање PDF-а није укључено за овај задатак (подразумевано искључено).", "이 작업에는 PDF 자동 가져오기가 활성화되어 있지 않습니다(기본값: 꺼짐)."],
      ["该文献缺少 DOI，无法自动获取。", "This article has no DOI; it cannot be fetched automatically.", "Cet article n’a pas de DOI ; la récupération automatique est impossible.", "У этой публикации нет DOI — автоматически получить текст нельзя.", "Este artículo no tiene DOI; no se puede obtener automáticamente.", "この文献にはDOIがなく、自動取得できません。", "Este artigo não tem DOI; não pode ser obtido automaticamente.", "Dieser Artikel hat keine DOI; ein automatischer Abruf ist nicht möglich.", "Овај чланак нема DOI, па се не може аутоматски преузети.", "이 문헌에는 DOI가 없어 자동으로 가져올 수 없습니다."],
      ["无法连接获取服务，请稍后重试。", "Cannot reach the fetch service; please try again later.", "Impossible de joindre le service de récupération ; réessayez plus tard.", "Служба загрузки недоступна; попробуйте позже.", "No se puede conectar con el servicio de obtención; inténtelo más tarde.", "取得サービスに接続できません。後でもう一度お試しください。", "Não é possível conectar ao serviço de busca; tente novamente mais tarde.", "Der Abrufdienst ist nicht erreichbar; bitte später erneut versuchen.", "Сервис за преузимање није доступан; покушајте касније.", "가져오기 서비스에 연결할 수 없습니다. 나중에 다시 시도하세요."],
      ["下载内容不是有效的 PDF。", "The downloaded content is not a valid PDF.", "Le contenu téléchargé n’est pas un PDF valide.", "Загруженный файл не является корректным PDF.", "El contenido descargado no es un PDF válido.", "ダウンロードした内容は有効なPDFではありません。", "O conteúdo baixado não é um PDF válido.", "Der heruntergeladene Inhalt ist kein gültiges PDF.", "Преузети садржај није важећи PDF.", "다운로드한 내용이 유효한 PDF가 아닙니다."],
      ["PDF 自动获取（DOI→Unpaywall）", "PDF auto-fetch (DOI→Unpaywall)", "Récupération automatique des PDF (DOI→Unpaywall)", "Автозагрузка PDF (DOI→Unpaywall)", "Obtención automática de PDF (DOI→Unpaywall)", "PDF自動取得（DOI→Unpaywall）", "Busca automática de PDF (DOI→Unpaywall)", "Automatischer PDF-Abruf (DOI→Unpaywall)", "Аутоматско преузимање PDF-а (DOI→Unpaywall)", "PDF 자동 가져오기(DOI→Unpaywall)"],
      ["启用 PDF 自动获取", "Enable PDF auto-fetch", "Activer la récupération automatique des PDF", "Включить автозагрузку PDF", "Activar la obtención automática de PDF", "PDF自動取得を有効にする", "Ativar a busca automática de PDF", "PDF-Abruf aktivieren", "Укључи аутоматско преузимање PDF-а", "PDF 자동 가져오기 활성화"],
      ["联系 email（Unpaywall 要求，非密钥）", "Contact email (required by Unpaywall; not a key)", "E-mail de contact (exigé par Unpaywall ; ce n’est pas une clé)", "Контактный e-mail (требуется Unpaywall; не ключ)", "Correo de contacto (lo exige Unpaywall; no es una clave)", "連絡用メール（Unpaywall必須、キーではない）", "E-mail de contato (exigido pelo Unpaywall; não é uma chave)", "Kontakt-E-Mail (von Unpaywall gefordert; kein Schlüssel)", "Контакт имејл (захтева Unpaywall; није кључ)", "연락용 이메일(Unpaywall 필수, 키가 아님)"],
      ["Unpaywall 端点（可自托管）", "Unpaywall endpoint (self-hostable)", "Point d’accès Unpaywall (auto-hébergeable)", "Эндпоинт Unpaywall (можно разместить у себя)", "Endpoint de Unpaywall (autoalojable)", "Unpaywallエンドポイント（自己ホスト可）", "Endpoint do Unpaywall (auto-hospedável)", "Unpaywall-Endpunkt (selbst hostbar)", "Unpaywall крајња тачка (могућ самостални хостинг)", "Unpaywall 엔드포인트(자체 호스팅 가능)"],
      ["Crossref 端点（可自托管）", "Crossref endpoint (self-hostable)", "Point d’accès Crossref (auto-hébergeable)", "Эндпоинт Crossref (можно разместить у себя)", "Endpoint de Crossref (autoalojable)", "Crossrefエンドポイント（自己ホスト可）", "Endpoint do Crossref (auto-hospedável)", "Crossref-Endpunkt (selbst hostbar)", "Crossref крајња тачка (могућ самостални хостинг)", "Crossref 엔드포인트(자체 호스팅 가능)"],
      ["保存设置", "Save settings", "Enregistrer les paramètres", "Сохранить настройки", "Guardar ajustes", "設定を保存", "Salvar configurações", "Einstellungen speichern", "Сачувај подешавања", "설정 저장"],
      ["设置已保存。", "Settings saved.", "Paramètres enregistrés.", "Настройки сохранены.", "Ajustes guardados.", "設定を保存しました。", "Configurações salvas.", "Einstellungen gespeichert.", "Подешавања су сачувана.", "설정이 저장되었습니다."],
      ["只有任务负责人可以修改该设置。", "Only the task owner can change this setting.", "Seul le responsable de la tâche peut modifier ce paramètre.", "Изменить настройку может только владелец задачи.", "Solo el responsable de la tarea puede cambiar este ajuste.", "この設定を変更できるのはタスクの責任者のみです。", "Somente o responsável pela tarefa pode alterar esta configuração.", "Nur der Aufgabenverantwortliche kann diese Einstellung ändern.", "Само власник задатка може мењати ово подешавање.", "작업 소유자만 이 설정을 변경할 수 있습니다."],
      ["设置已被他人更新，请刷新后重试。", "The settings were changed elsewhere; refresh and try again.", "Les paramètres ont été modifiés ailleurs ; actualisez puis réessayez.", "Настройки были изменены другим пользователем; обновите и повторите.", "Los ajustes fueron modificados en otra parte; actualice e inténtelo de nuevo.", "設定が他の場所で更新されました。更新して再試行してください。", "As configurações foram alteradas em outro lugar; atualize e tente novamente.", "Die Einstellungen wurden anderweitig geändert; bitte aktualisieren und erneut versuchen.", "Подешавања су негде измењена; освежите и покушајте поново.", "설정이 다른 곳에서 변경되었습니다. 새로 고침 후 다시 시도하세요."],
      ["出站访问范围：{0}", "Outbound access: {0}", "Accès réseau sortant : {0}", "Область исходящих запросов: {0}", "Acceso de salida: {0}", "送信アクセス範囲：{0}", "Acesso de saída: {0}", "Ausgehender Zugriff: {0}", "Опсег одлазног приступа: {0}", "아웃바운드 접근 범위: {0}"],
      ["仅访问以上端点，以及 Unpaywall 响应中给出的开放获取 PDF 链接（仅限 http/https 的公网地址；本机与内网地址一律拒绝）。", "Only the endpoints above, plus open-access PDF links returned by Unpaywall (http/https public addresses only; local and private addresses are always refused).", "Seuls les points d’accès ci-dessus, ainsi que les liens PDF en accès ouvert renvoyés par Unpaywall (adresses publiques http/https uniquement ; les adresses locales et privées sont toujours refusées).", "Доступ только к указанным выше эндпоинтам и к открытым PDF-ссылкам из ответа Unpaywall (только публичные адреса http/https; локальные и внутренние адреса всегда отклоняются).", "Solo se accede a los endpoints anteriores y a los enlaces PDF de acceso abierto devueltos por Unpaywall (solo direcciones públicas http/https; las locales y privadas siempre se rechazan).", "アクセスするのは上記エンドポイントと、Unpaywallが返すオープンアクセスPDFリンク（http/https の公開アドレスのみ。ローカル／内部アドレスは常に拒否）のみです。", "Acessa apenas os endpoints acima e os links de PDF em acesso aberto retornados pelo Unpaywall (somente endereços públicos http/https; endereços locais e privados são sempre recusados).", "Zugegriffen wird nur auf die obigen Endpunkte und die von Unpaywall gelieferten Open-Access-PDF-Links (nur öffentliche http/https-Adressen; lokale und interne Adressen werden immer abgelehnt).", "Приступа се само горњим крајњим тачкама и PDF везама отвореног приступа из Unpaywall одговора (само јавне http/https адресе; локалне и интерне адресе се увек одбијају).", "위 엔드포인트와 Unpaywall 응답의 오픈액세스 PDF 링크(http/https 공개 주소만, 로컬·내부 주소는 항상 거부)에만 접근합니다."],
      ["批量获取", "Batch fetch", "Récupération par lot", "Пакетная загрузка", "Obtención por lotes", "一括取得", "Busca em lote", "Stapelabruf", "Групно преузимање", "일괄 가져오기"],
      ["篇数上限（1-50）", "Article limit (1–50)", "Limite d’articles (1-50)", "Лимит публикаций (1–50)", "Límite de artículos (1-50)", "件数上限（1〜50）", "Limite de artigos (1-50)", "Artikellimit (1–50)", "Ограничење чланака (1-50)", "문헌 수 제한(1-50)"],
      ["批量完成：成功 {0} · 无开放 PDF {1} · 失败 {2} · 跳过 {3}", "Batch done: {0} saved · {1} no OA · {2} failed · {3} skipped", "Lot terminé : {0} obtenus · {1} sans OA · {2} échecs · {3} ignorés", "Пакет завершён: получено {0} · без OA {1} · ошибок {2} · пропущено {3}", "Lote terminado: {0} obtenidos · {1} sin OA · {2} fallidos · {3} omitidos", "一括完了：成功 {0}・OAなし {1}・失敗 {2}・スキップ {3}", "Lote concluído: {0} obtidos · {1} sem OA · {2} falhas · {3} ignorados", "Stapel fertig: {0} abgerufen · {1} ohne OA · {2} fehlgeschlagen · {3} übersprungen", "Група завршена: преузето {0} · без OA {1} · грешака {2} · прескочено {3}", "일괄 완료: 성공 {0} · OA 없음 {1} · 실패 {2} · 건너뜀 {3}"],
      ["另有 {0} 条内容无效，{1} 条未找到。", "{0} had invalid content, {1} were not found.", "{0} avaient un contenu invalide, {1} introuvables.", "У {0} некорректное содержимое, {1} не найдены.", "{0} tenían contenido no válido, {1} no encontrados.", "{0} 件は内容が無効、{1} 件は未検出です。", "{0} tinham conteúdo inválido, {1} não encontrados.", "{0} hatten ungültigen Inhalt, {1} wurden nicht gefunden.", "Код {0} неисправан садржај, {1} није пронађено.", "{0}건은 내용이 유효하지 않고, {1}건은 찾지 못했습니다."],
      ["该文献已有全文 PDF。继续将覆盖既有文件，是否继续？", "This article already has a full-text PDF. Continuing will overwrite it. Continue?", "Cet article a déjà un PDF en texte intégral. Continuer l’écrasera. Continuer ?", "У этой публикации уже есть полный текст PDF. Продолжение перезапишет его. Продолжить?", "Este artículo ya tiene un PDF de texto completo. Continuar lo sobrescribirá. ¿Continuar?", "この文献には既に全文PDFがあります。続行すると上書きされます。続行しますか？", "Este artigo já tem um PDF completo. Continuar irá substituí-lo. Continuar?", "Dieser Artikel hat bereits ein Volltext-PDF. Fortfahren überschreibt es. Fortfahren?", "Овај чланак већ има пун текст у PDF-у. Наставак ће га заменити. Наставити?", "이 문헌에는 이미 전문 PDF가 있습니다. 계속하면 덮어씁니다. 계속할까요?"],
      ["开放获取链接指向本机或内网地址，已拒绝访问。", "The open-access link points to a local or private address and was refused.", "Le lien en accès ouvert pointe vers une adresse locale ou privée ; accès refusé.", "Ссылка OA ведёт на локальный или внутренний адрес — доступ отклонён.", "El enlace de acceso abierto apunta a una dirección local o privada; acceso rechazado.", "オープンアクセスのリンクがローカル／内部アドレスを指すため拒否しました。", "O link de acesso aberto aponta para um endereço local ou privado; acesso recusado.", "Der Open-Access-Link zeigt auf eine lokale oder interne Adresse; Zugriff abgelehnt.", "Веза отвореног приступа води на локалну или интерну адресу; приступ одбијен.", "오픈액세스 링크가 로컬 또는 내부 주소를 가리켜 거부했습니다."],
      ["当前阶段已暂停或该文献不在当前来源范围内，暂不能写入全文。", "This stage is paused or the article is outside the current source scope, so the full text cannot be written now.", "Cette étape est en pause ou l’article est hors du périmètre de sources actuel ; impossible d’écrire le texte intégral.", "Этап приостановлен или публикация вне текущего диапазона источников — запись полного текста невозможна.", "La etapa está en pausa o el artículo está fuera del alcance de fuentes actual; no se puede escribir el texto completo.", "この段階は一時停止中か、文献が現在のソース範囲外のため全文を書き込めません。", "Esta etapa está pausada ou o artigo está fora do escopo de fontes atual; não é possível gravar o texto completo.", "Diese Stufe ist pausiert oder der Artikel liegt außerhalb des aktuellen Quellenbereichs; Volltext kann nicht geschrieben werden.", "Ова фаза је паузирана или је чланак ван тренутног опсега извора; пун текст се не може уписати.", "이 단계가 일시 중지되었거나 문헌이 현재 소스 범위를 벗어나 전문을 기록할 수 없습니다."],
      ["连续无法连接上游，剩余文献已跳过。", "Upstream unreachable; the remaining articles were skipped.", "Amont injoignable ; les articles restants ont été ignorés.", "Верхний сервис недоступен; остальные публикации пропущены.", "No se puede acceder al servicio; se omitieron los artículos restantes.", "上流に接続できないため、残りの文献はスキップされました。", "Serviço upstream inacessível; os artigos restantes foram ignorados.", "Upstream nicht erreichbar; die übrigen Artikel wurden übersprungen.", "Горњи сервис недоступан; преостали чланци су прескочени.", "업스트림에 연결할 수 없어 나머지 문헌은 건너뛰었습니다."],
      ["没有可获取的文献（需要有 DOI）。", "No fetchable articles (DOI required).", "Aucun article récupérable (DOI requis).", "Нет публикаций для загрузки (нужен DOI).", "No hay artículos que se puedan obtener (se necesita DOI).", "取得可能な文献がありません（DOIが必要です）。", "Nenhum artigo que possa ser obtido (é necessário DOI).", "Keine abrufbaren Artikel (DOI erforderlich).", "Нема чланака за преузимање (потребан је DOI).", "가져올 수 있는 문헌이 없습니다(DOI 필요)."],
      ["元数据建议", "Metadata suggestions", "Suggestions de métadonnées", "Предложения метаданных", "Sugerencias de metadatos", "メタデータ提案", "Sugestões de metadados", "Metadaten-Vorschläge", "Предлози метаподатака", "메타데이터 제안"],
      ["建议仅供预览，不会写入数据库；采纳请通过既有编辑路径。", "Suggestions are preview-only and never written to the database; adopt them via the existing edit path.", "Les suggestions sont en aperçu uniquement et ne sont jamais écrites dans la base ; adoptez-les via le chemin d’édition existant.", "Предложения только для просмотра и никогда не записываются в базу; применяйте их через существующий путь редактирования.", "Las sugerencias son solo de vista previa y nunca se escriben en la base de datos; adóptelas mediante la ruta de edición existente.", "提案はプレビュー用で、データベースには書き込まれません。採用は既存の編集経路で行ってください。", "As sugestões são apenas visualização e nunca são gravadas no banco de dados; adote-as pelo caminho de edição existente.", "Vorschläge sind nur zur Vorschau und werden nie in die Datenbank geschrieben; übernehmen Sie sie über den bestehenden Bearbeitungspfad.", "Предлози су само за преглед и никад се не уписују у базу; усвојите их кроз постојећи пут уређивања.", "제안은 미리보기 전용이며 데이터베이스에 절대 기록되지 않습니다. 기존 편집 경로를 통해 채택하세요."],
      ["复制", "Copy", "Copier", "Копировать", "Copiar", "コピー", "Copiar", "Kopieren", "Копирај", "복사"],
      ["已复制。", "Copied.", "Copié.", "Скопировано.", "Copiado.", "コピーしました。", "Copiado.", "Kopiert.", "Копирано.", "복사되었습니다."],
      ["打开 PDF", "Open PDF", "Ouvrir le PDF", "Открыть PDF", "Abrir PDF", "PDFを開く", "Abrir PDF", "PDF öffnen", "Отвори PDF", "PDF 열기"],
      ["PDF 获取未启用", "PDF auto-fetch disabled", "Récupération des PDF désactivée", "Автозагрузка PDF отключена", "Obtención de PDF desactivada", "PDF自動取得は無効", "Busca de PDF desativada", "PDF-Abruf deaktiviert", "Преузимање PDF-а искључено", "PDF 자동 가져오기 비활성화"],
      ["PDF 获取已启用", "PDF auto-fetch enabled", "Récupération des PDF activée", "Автозагрузка PDF включена", "Obtención de PDF activada", "PDF自動取得は有効", "Busca de PDF ativada", "PDF-Abruf aktiviert", "Преузимање PDF-а укључено", "PDF 자동 가져오기 활성화"],
      ["已有全文 {0}", "{0} already had full text", "{0} avaient déjà un texte intégral", "У {0} уже был полный текст", "{0} ya tenían texto completo", "既に全文あり {0} 件", "{0} já tinham texto completo", "{0} hatten bereits Volltext", "Код {0} већ има пун текст", "이미 전문 있음 {0}건"],
      ["键非法 {0}", "{0} invalid keys", "{0} clés invalides", "{0} недопустимых ключей", "{0} claves no válidas", "不正なキー {0} 件", "{0} chaves inválidas", "{0} ungültige Schlüssel", "{0} неисправних кључева", "잘못된 키 {0}건"],
      ["工作流拦截 {0}", "{0} blocked by workflow", "{0} bloqués par le flux de travail", "{0} заблокировано рабочим процессом", "{0} bloqueados por el flujo de trabajo", "ワークフローにより {0} 件ブロック", "{0} bloqueados pelo fluxo de trabalho", "{0} durch Workflow blockiert", "{0} блокирано током рада", "워크플로에 의해 차단 {0}건"],
      ["上游不可达中止 {0}", "{0} skipped after upstream failure", "{0} ignorés après échec de l’amont", "{0} пропущено после сбоя верхнего сервиса", "{0} omitidos tras fallo del servicio", "上流障害で {0} 件スキップ", "{0} ignorados após falha do serviço", "{0} nach Upstream-Fehler übersprungen", "{0} прескочено након слома горњег сервиса", "업스트림 장애로 건너뜀 {0}건"],
      ["跳过明细：{0}", "Skipped: {0}", "Ignorés : {0}", "Пропущено: {0}", "Omitidos: {0}", "スキップ内訳：{0}", "Ignorados: {0}", "Übersprungen: {0}", "Прескочено: {0}", "건너뜀 상세: {0}"],
      ["开放获取链接的域名无法解析，请稍后重试。", "The open-access link’s domain could not be resolved; please try again later.", "Le domaine du lien en accès ouvert est introuvable ; réessayez plus tard.", "Не удалось разрешить домен ссылки OA; попробуйте позже.", "No se pudo resolver el dominio del enlace de acceso abierto; inténtelo más tarde.", "オープンアクセスリンクのドメインを解決できません。後でもう一度お試しください。", "Não foi possível resolver o domínio do link de acesso aberto; tente novamente mais tarde.", "Die Domain des Open-Access-Links konnte nicht aufgelöst werden; bitte später erneut versuchen.", "Домен везе отвореног приступа није могуће разрешити; покушајте касније.", "오픈액세스 링크의 도메인을 확인할 수 없습니다. 나중에 다시 시도하세요."],
      ["缺少联系 email，请负责人在任务设置中补填。", "The contact email is missing; the owner must add it in task settings.", "L’e-mail de contact est manquant ; le responsable doit l’ajouter dans les paramètres.", "Контактный e-mail отсутствует; владелец должен добавить его в настройках.", "Falta el correo de contacto; el responsable debe añadirlo en los ajustes.", "連絡用メールが未設定です。責任者がタスク設定で入力してください。", "Falta o e-mail de contato; o responsável deve adicioná-lo nas configurações.", "Die Kontakt-E-Mail fehlt; der Verantwortliche muss sie in den Aufgaben­einstellungen ergänzen.", "Контакт имејл недостаје; власник га мора додати у подешавањима задатка.", "연락용 이메일이 없습니다. 소유자가 작업 설정에서 입력해야 합니다."],
      ["端点地址不合法（需 http/https 且含主机名）。", "Invalid endpoint URL (http/https with a host is required).", "URL de point d’accès invalide (http/https avec un hôte requis).", "Недопустимый адрес эндпоинта (нужен http/https с именем хоста).", "URL del endpoint no válido (se requiere http/https con host).", "エンドポイントURLが不正です（ホスト名付きのhttp/httpsが必要）。", "URL do endpoint inválida (http/https com host é obrigatório).", "Ungültige Endpunkt-URL (http/https mit Host erforderlich).", "Неисправан URL крајње тачке (потребан http/https са именом хоста).", "엔드포인트 URL이 잘못되었습니다(호스트가 포함된 http/https 필요)."],
      ["该文献在当前筛选库中不存在。", "This article does not exist in the current screening library.", "Cet article n’existe pas dans la bibliothèque de tri actuelle.", "Эта публикация отсутствует в текущей базе скрининга.", "Este artículo no existe en la biblioteca de cribado actual.", "この文献は現在のスクリーニングライブラリに存在しません。", "Este artigo não existe na biblioteca de triagem atual.", "Dieser Artikel existiert in der aktuellen Screening-Bibliothek nicht.", "Овај чланак не постоји у текућој библиотеци скрининга.", "이 문헌은 현재 스크리닝 라이브러리에 없습니다."],
      ["邮箱格式不正确。", "Invalid email format.", "Format d’e-mail invalide.", "Неверный формат e-mail.", "Formato de correo no válido.", "メールアドレスの形式が正しくありません。", "Formato de e-mail inválido.", "Ungültiges E-Mail-Format.", "Неисправан формат имејла.", "이메일 형식이 올바르지 않습니다."],
      ["出现未知错误，请稍后重试。", "Unexpected error; please try again later.", "Erreur inattendue ; réessayez plus tard.", "Непредвиденная ошибка; повторите позже.", "Error inesperado; inténtelo de nuevo más tarde.", "予期しないエラーです。後でもう一度お試しください。", "Erro inesperado; tente novamente mais tarde.", "Unerwarteter Fehler; bitte später erneut versuchen.", "Неочекивана грешка; покушајте касније.", "예기치 않은 오류입니다. 나중에 다시 시도하세요."],
      ["请先选择任务。", "Select a task first.", "Veuillez d’abord sélectionner une tâche.", "Сначала выберите задачу.", "Seleccione primero una tarea.", "先にタスクを選択してください。", "Selecione uma tarefa primeiro.", "Wählen Sie zuerst eine Aufgabe aus.", "Најпре изаберите задатак.", "먼저 작업을 선택하세요."]
    ]);

    /* =====================================================================
     * 二、自包含助手（DOM 全走 textContent；异常自捕获）
     * ===================================================================== */
    const mk = (tag, text, props, ...children) => {
      const node = document.createElement(tag);
      if (text !== undefined && text !== null && text !== false) node.textContent = String(text);
      for (const key in (props || {})) {
        const value = props[key];
        if (value === undefined || value === null) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = String(value);
        else if (key.slice(0, 2) === 'on' && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value);
      }
      for (const child of children) {
        if (child === undefined || child === null || child === false) continue;
        node.append(child);
      }
      return node;
    };
    const btn = (label, onClick, cls) => {
      const node = mk('button', T(label), { type: 'button', class: cls || 'btn b-out sm' });
      node.addEventListener('click', safe(onClick));
      return node;
    };
    function safe(fn) {
      return (...args) => {
        try { return fn(...args); } catch (error) { /* 单点故障不外溢 */ }
      };
    }
    const toastMsg = (text, kind) => {
      try { if (typeof toast === 'function') toast(text, kind || 'warn'); } catch (_) {}
    };
    const taskApi = () => (typeof S !== 'undefined' && S.task)
      ? '/api/tasks/' + encodeURIComponent(S.task.task_id) : '';
    /* 查询串拆两段：单篇路径的 screener 用 '?'；overwrite=1 时先有 '?' 再拼 '&'。 */
    const scrValue = () => (typeof S !== 'undefined' && S.screener)
      ? 'screener=' + encodeURIComponent(S.screener) : '';
    const screenerQuery = () => { const value = scrValue(); return value ? '?' + value : ''; };
    const scrSep = () => (scrValue() ? '&' + scrValue() : '');

    /* =====================================================================
     * 三、任务级判定（settings 一次拉取；未启用 → 全部入口零渲染）
     * ===================================================================== */
    let settings = null;
    let scopeKey = '';
    let detectTicket = 0;

    async function detect() {
      const scope = taskApi();
      if (!scope) { settings = null; return; }
      const key = scope + '|' + ((typeof S !== 'undefined' && S.screener) || '');
      if (key === scopeKey && settings !== null) return settings;
      const ticket = ++detectTicket;
      try {
        const result = await api(scope + '/pdf-fetch/settings');
        if (ticket !== detectTicket) return settings;
        settings = result && typeof result === 'object' ? result : null;
        scopeKey = key;
      } catch (_) { settings = null; scopeKey = ''; }   // 静默降级：后端不可达即不渲染
      return settings;
    }

    /* 错误 code → 本地化提示（后端 pdf_fetch_api.py 的结构化 code） */
    function messageFor(error) {
      const code = error && error.code;
      if (code === 'pdf_fetch_disabled') return T('任务未启用 PDF 自动获取（默认关）。');
      if (code === 'pdf_fetch_doi_missing') return T('该文献缺少 DOI，无法自动获取。');
      if (code === 'pdf_fetch_invalid_pdf') return T('下载内容不是有效的 PDF。');
      if (code === 'pdf_fetch_unsafe_url') return T('开放获取链接指向本机或内网地址，已拒绝访问。');
      if (code === 'pdf_fetch_email_missing' || code === 'pdf_fetch_email_required')
        return T('缺少联系 email，请负责人在任务设置中补填。');
      if (code === 'pdf_fetch_email_invalid') return T('邮箱格式不正确。');
      if (code === 'pdf_fetch_base_url_invalid' || code === 'pdf_fetch_base_url_insecure')
        return T('端点地址不合法（需 http/https 且含主机名）。');
      if (code === 'pdf_fetch_base_url_blocked') return T('taskPackage.pdfEndpointBlocked');
      if (code === 'pdf_fetch_settings_version_conflict') return T('设置已被他人更新，请刷新后重试。');
      if (code === 'pdf_fetch_article_missing') return T('该文献在当前筛选库中不存在。');
      if (code === 'pdf_fetch_trace_conflict') return T('出现未知错误，请稍后重试。');
      if (code === 'stage_paused' || code === 'article_outside_scope' || code === 'task_read_only'
          || code === 'article_not_eligible')
        return T('当前阶段已暂停或该文献不在当前来源范围内，暂不能写入全文。');
      if (error && error.status === 403) return T('只有任务负责人可以修改该设置。');
      if (error && error.status === 502) return T('无法连接获取服务，请稍后重试。');
      return T('出现未知错误，请稍后重试。');
    }

    /* 以鉴权请求取回 PDF 字节再本地打开：全站鉴权只有 Authorization: Bearer，
       浏览器直接导航 <a href> 不会带该头（必然 401）。既有 PDF 打开路径同法
       （index.html 的 blob: true）。 */
    async function openPdfBlob(articleKey) {
      const base = taskApi();
      if (!base) return;
      const blob = await api(base + '/fulltext/' + encodeURIComponent(articleKey) + screenerQuery(),
                             { blob: true });
      const url = URL.createObjectURL(blob);
      /* r3 复审⑦：``window.open(url,'_blank','noopener')`` 按规范恒返回 null，
         旧写法会落进兜底分支再点一次 anchor → 用户看到两个标签页。这里只用
         单一路径：先试 window.open（不带 noopener 特性），成功就断 opener。 */
      const opened = typeof window.open === 'function' ? window.open(url, '_blank') : null;
      if (opened) {
        try { opened.opener = null; } catch (_) {}
      } else {
        const link = mk('a', T('打开 PDF'), { href: url, target: '_blank', rel: 'noopener' });
        document.body.append(link);
        link.click();
        link.remove();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    /* =====================================================================
     * 四、初筛文献卡「获取全文」按钮（#scrBody MutationObserver 追加式挂载）
     * ===================================================================== */
    function currentArticle() {
      if (typeof S === 'undefined' || !Array.isArray(S.articles)) return null;
      return S.articles[S.scrIdx] || null;
    }

    async function fetchOne(status) {
      const article = currentArticle();
      if (!article || !article.zotero_key) return;
      const base = taskApi();
      if (!base) return;
      const key = article.zotero_key;
      status.textContent = T('获取中…');
      try {
        let result;
        try {
          result = await api(base + '/fulltext/' + encodeURIComponent(key) + '/fetch' + screenerQuery(),
                             { method: 'POST' });
        } catch (firstError) {
          /* 不静默覆盖：已有全文时后端 409，确认后才带 overwrite=1 重发。 */
          if (!firstError || firstError.code !== 'pdf_fetch_would_replace') throw firstError;
          /* 前端 ApiError 只透传 code/reasons/fields，不携带 detail.existing，
             因此这里用不带页数的确认文案（后端仍回传 existing 供其它客户端用）。 */
          const confirmed = typeof confirm === 'function'
            ? confirm(T('该文献已有全文 PDF。继续将覆盖既有文件，是否继续？'))
            : false;
          if (!confirmed) { status.textContent = ''; return; }
          status.textContent = T('获取中…');
          result = await api(base + '/fulltext/' + encodeURIComponent(key) + '/fetch?overwrite=1' + scrSep(),
                             { method: 'POST' });
        }
        if (result && result.outcome === 'saved') {
          status.textContent = T('已获取全文（{0} 页）。', String(result.n_pages)) + ' ';
          const openButton = btn('打开 PDF', async () => {
            try { await openPdfBlob(key); }
            catch (error) { toastMsg(error.status === 404 ? T('taskPackage.fulltext.recovery') : error.message, 'warn'); }
          });
          status.append(openButton);
          toastMsg(T('已获取全文（{0} 页）。', String(result.n_pages)), 'inc');
        } else if (result && result.outcome === 'no_oa') {
          status.textContent = T('未找到开放获取的 PDF。');
        } else if (result && result.outcome === 'blocked') {
          status.textContent = T('开放获取链接指向本机或内网地址，已拒绝访问。');
        } else if (result && result.outcome === 'skipped' && result.reason === 'already_has_pdf') {
          status.textContent = '';
        } else {
          status.textContent = T('出现未知错误，请稍后重试。');
        }
      } catch (error) {
        status.textContent = messageFor(error);
      }
    }

    async function showSuggestions(host) {
      const article = currentArticle();
      const base = taskApi();
      if (!article || !article.zotero_key || !base) return;
      host.textContent = T('获取中…');
      try {
        const result = await api(base + '/fulltext/' + encodeURIComponent(article.zotero_key) +
          '/metadata-suggestions' + screenerQuery());
        host.replaceChildren(mk('p', T('建议仅供预览，不会写入数据库；采纳请通过既有编辑路径。'), { class: 'hint' }));
        const suggestions = (result && result.suggestions) || {};
        const fields = ['doi', 'year', 'abstract'];
        let shown = 0;
        for (const field of fields) {
          if (!(field in suggestions)) continue;
          shown++;
          const value = String(suggestions[field]);
          const line = mk('div', null, { class: 'row', style: 'gap:6px;align-items:flex-start;margin:4px 0' });
          line.append(mk('span', field + ': ' + value.slice(0, 300), { class: 'hint', style: 'flex:1;overflow-wrap:anywhere' }));
          line.append(btn('复制', async () => {
            try {
              if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(value);
              toastMsg(T('已复制。'), 'inc');
            } catch (_) { toastMsg(T('出现未知错误，请稍后重试。')); }
          }));
          host.append(line);
        }
        if (!shown) host.append(mk('p', '—', { class: 'hint' }));
      } catch (error) {
        host.replaceChildren(mk('p', messageFor(error), { class: 'hint' }));
      }
    }

    function decorateScreenCard() {
      if (!settings || !settings.enabled) return;
      const body = document.getElementById('scrBody');
      if (!body || !body.querySelector('.scr-decisions')) return;
      if (document.getElementById('rfPdfFetchRow')) return;
      const article = currentArticle();
      if (!article || !article.zotero_key) return;
      const row = mk('div', null, { class: 'row', id: 'rfPdfFetchRow', style: 'gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap' });
      const status = mk('span', '', { class: 'hint', role: 'status', style: 'min-width:0;overflow-wrap:anywhere' });
      const hasDoi = !!(article.doi && String(article.doi).trim());
      const fetchButton = btn('获取全文', () => fetchOne(status));
      if (!hasDoi) {
        fetchButton.disabled = true;
        fetchButton.title = T('该文献缺少 DOI，无法自动获取。');
      }
      row.append(fetchButton, btn('元数据建议', () => {
        const host = document.getElementById('rfPdfFetchSuggestions');
        if (host) showSuggestions(host);
      }));
      row.append(status);
      const suggestHost = mk('div', null, { id: 'rfPdfFetchSuggestions', class: 'hint', style: 'max-width:100%' });
      body.append(row, suggestHost);
    }

    /* =====================================================================
     * 五、设置卡（#wf-panel-coding：开关/email/自托管端点/披露行/批量入口）
     * ===================================================================== */
    const codingPanel = document.getElementById('wf-panel-coding');
    const card = codingPanel ? mk('section', null, { class: 'card', id: 'rfPdfFetchCard' }) : null;
    if (card) codingPanel.append(card);

    function renderCard() {
      if (!card) return;
      card.replaceChildren(mk('h3', T('PDF 自动获取（DOI→Unpaywall）'), { style: 'margin:0 0 6px' }));
      if (typeof S === 'undefined' || !S.task) {
        card.append(mk('p', T('请先选择任务。'), { class: 'hint' }));
        return;
      }
      const data = settings || {};
      card.append(mk('p', data.enabled ? T('PDF 获取已启用') : T('PDF 获取未启用'), { class: 'hint' }));
      if (!settings) return;   /* settings 拉取失败：按默认关处理，不渲染表单 */
      /* 出站披露行：后端声明的端点 + PDF 链接范围（隐私红线常显）。
         r3 复审⑧：后端 pdf_hosts_note 是这层披露的正典口径（含「本机与内网
         一律拒绝」），中文界面直接用它，避免实现改了文案没跟上；其它语言用
         本行译文（与后端同口径）。 */
      const endpoints = Array.isArray(data.outbound_endpoints) ? data.outbound_endpoints.join(' · ') : '';
      if (endpoints) {
        card.append(mk('p', T('出站访问范围：{0}', endpoints), { class: 'hint', style: 'overflow-wrap:anywhere' }));
        const lang = (typeof document !== 'undefined' && document.documentElement
          && document.documentElement.lang) || '';
        const note = (typeof data.pdf_hosts_note === 'string' && data.pdf_hosts_note.trim()
          && lang.toLowerCase().startsWith('zh'))
          ? data.pdf_hosts_note.trim()
          : T('仅访问以上端点，以及 Unpaywall 响应中给出的开放获取 PDF 链接（仅限 http/https 的公网地址；本机与内网地址一律拒绝）。');
        card.append(mk('p', note, { class: 'hint' }));
      }

      /* ---- 表单（owner；非 owner 保存时由 403 提示） ---- */
      const enabled = mk('input', '', { type: 'checkbox', 'data-rf-name': 'enabled' });
      enabled.checked = !!data.enabled;
      const email = mk('input', '', { class: 'input', type: 'email', maxlength: '254', translate: 'no', 'data-rf-name': 'email', 'aria-label': T('联系 email（Unpaywall 要求，非密钥）') });
      email.value = data.email || '';
      const unpaywallBase = mk('input', '', { class: 'input', maxlength: '500', translate: 'no', 'data-rf-name': 'unpaywall_base_url', 'aria-label': T('Unpaywall 端点（可自托管）') });
      unpaywallBase.value = data.unpaywall_base_url || '';
      const crossrefBase = mk('input', '', { class: 'input', maxlength: '500', translate: 'no', 'data-rf-name': 'crossref_base_url', 'aria-label': T('Crossref 端点（可自托管）') });
      crossrefBase.value = data.crossref_base_url || '';
      const formStatus = mk('p', '', { class: 'hint', role: 'status' });
      const save = btn('保存设置', async () => {
        save.disabled = true;
        try {
          const result = await api(taskApi() + '/pdf-fetch/settings', {
            method: 'PUT',
            body: {
              enabled: enabled.checked,
              email: email.value.trim(),
              unpaywall_base_url: unpaywallBase.value.trim() || undefined,
              crossref_base_url: crossrefBase.value.trim() || undefined,
              expected_version: settings.version
            }
          });
          settings = result;
          formStatus.textContent = T('设置已保存。');
          toastMsg(T('设置已保存。'), 'inc');
          renderCard();
          decorateScreenCard();
        } catch (error) {
          formStatus.textContent = messageFor(error);
        } finally {
          save.disabled = false;
        }
      });
      const field = (label, control) => mk('label', null, { style: 'display:grid;gap:4px' },
        mk('span', T(label), { class: 'hint' }), control);
      card.append(
        mk('div', null, { class: 'row', style: 'gap:8px;align-items:center;margin:4px 0' },
          mk('label', null, { class: 'lbl' }, enabled, document.createTextNode(' ' + T('启用 PDF 自动获取')))),
        mk('div', null, { style: 'display:grid;gap:8px;margin:10px 0;max-width:560px' },
          field('联系 email（Unpaywall 要求，非密钥）', email),
          field('Unpaywall 端点（可自托管）', unpaywallBase),
          field('Crossref 端点（可自托管）', crossrefBase)),
        save, formStatus);

      /* ---- 批量入口（显式手动触发；启用后才渲染） ---- */
      if (!settings.enabled) return;
      const limit = mk('input', '', { class: 'input', type: 'number', min: '1', max: '50', value: '10', style: 'max-width:110px', 'aria-label': T('篇数上限（1-50）') });
      const batchStatus = mk('p', '', { class: 'hint', role: 'status' });
      const batchButton = btn('批量获取', async () => {
        const base = taskApi();
        if (!base) return;
        const n = Math.max(1, Math.min(50, parseInt(limit.value, 10) || 10));
        batchButton.disabled = true;
        batchStatus.textContent = T('获取中…');
        try {
          const list = await api(base + '/articles' + screenerQuery());
          const withDoi = ((list && list.articles) || [])
            .filter(a => a && a.doi && String(a.doi).trim())
            .slice(0, n);
          if (!withDoi.length) {
            batchStatus.textContent = T('没有可获取的文献（需要有 DOI）。');
            return;
          }
          const result = await api(base + '/fulltext/fetch-batch' + screenerQuery(), {
            method: 'POST', body: { keys: withDoi.map(a => a.zotero_key) }
          });
          const summary = (result && result.summary) || {};
          batchStatus.textContent = T('批量完成：成功 {0} · 无开放 PDF {1} · 失败 {2} · 跳过 {3}',
            String(summary.saved || 0), String(summary.no_oa || 0),
            String(summary.failed || 0), String(summary.skipped || 0));
          /* 后端 summary 还有 invalid / missing / blocked / unresolvable /
             trace_conflict：不展示会让这些条目「凭空消失」，成功态显得比实际好。 */
          const extra = (summary.invalid || 0) + (summary.blocked || 0)
            + (summary.unresolvable || 0) + (summary.trace_conflict || 0);
          const missing = summary.missing || 0;
          if (extra || missing) {
            batchStatus.append(document.createTextNode(
              ' ' + T('另有 {0} 条内容无效，{1} 条未找到。', String(extra), String(missing))));
          }
          /* r3 复审⑩：skipped 混着四种原因（已有全文/键非法/工作流拦截/熔断），
             必须按 reason 拆开展示，否则用户会把「阶段暂停」误读成「没有 DOI」。 */
          const reasons = {};
          for (const row of ((result && result.results) || [])) {
            if (row && row.outcome === 'skipped' && row.reason) {
              reasons[row.reason] = (reasons[row.reason] || 0) + 1;
            }
          }
          const breakdown = [];
          if (reasons.already_has_pdf) breakdown.push(T('已有全文 {0}', String(reasons.already_has_pdf)));
          if (reasons.invalid_key) breakdown.push(T('键非法 {0}', String(reasons.invalid_key)));
          const workflowBlocked = (reasons.stage_paused || 0) + (reasons.article_outside_scope || 0)
            + (reasons.task_read_only || 0) + (reasons.article_not_eligible || 0)
            + (reasons.workflow_blocked || 0);
          if (workflowBlocked) breakdown.push(T('工作流拦截 {0}', String(workflowBlocked)));
          if (reasons.aborted_upstream_unreachable) {
            breakdown.push(T('上游不可达中止 {0}', String(reasons.aborted_upstream_unreachable)));
          }
          if (breakdown.length) {
            batchStatus.append(document.createTextNode(' ' + T('跳过明细：{0}', breakdown.join(' · '))));
          }
          if (result && result.aborted) {
            batchStatus.append(document.createTextNode(' ' + T('连续无法连接上游，剩余文献已跳过。')));
          }
        } catch (error) {
          batchStatus.textContent = messageFor(error);
        } finally {
          batchButton.disabled = false;
        }
      });
      card.append(mk('h4', T('批量获取'), { style: 'margin:10px 0 4px' }),
        mk('div', null, { class: 'row', style: 'gap:8px;align-items:center' }, limit, batchButton),
        batchStatus);
    }

    /* =====================================================================
     * 六、接线（任务/语言切换重绘；初筛卡 MutationObserver）
     * ===================================================================== */
    if (typeof MutationObserver !== 'undefined') {
      const scrBody = document.getElementById('scrBody');
      if (scrBody) new MutationObserver(safe(decorateScreenCard)).observe(scrBody, { childList: true });
    }
    document.addEventListener('reviewflow:task-context-changed', safe(() => {
      settings = null; scopeKey = '';
      detect().then(safe(() => { renderCard(); decorateScreenCard(); })).catch(() => {});
    }));
    document.addEventListener('reviewflow:language-changed', safe(renderCard));
    detect().then(safe(() => { renderCard(); decorateScreenCard(); })).catch(() => {});
  } catch (_) { /* 自包含：初始化失败不影响主应用 */ }
})();
