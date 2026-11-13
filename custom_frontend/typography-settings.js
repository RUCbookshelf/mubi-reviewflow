/* Optional browser-local typography. PDF glyph layout and product controls are untouched. */
(function () {
  'use strict';
  const root = document.documentElement, choiceKey = 'rf_typography_choice_v1';
  const databaseName = 'reviewflow-typography-v1', limit = 32 * 1024 * 1024;
  const fonts = [
    {id:'noto-sans-sc',name:'Noto Sans SC · 无衬线',family:'RF Noto Sans SC',file:'NotoSansSC-Variable.ttf',weight:'100 900'},
    {id:'noto-serif-sc',name:'Noto Serif SC · 衬线',family:'RF Noto Serif SC',file:'NotoSerifSC-Regular.ttf',weight:'400'},
    {id:'wenkai',name:'霞鹜文楷 Lite · LXGW WenKai Lite',family:'RF LXGW WenKai',file:'LXGWWenKaiLite-Regular.ttf',weight:'400'},
    {id:'inter',name:'Inter + Noto Sans SC',family:'RF Inter',file:'InterVariable.woff2',weight:'100 900',fallback:'noto-sans-sc'},
    {id:'source-serif',name:'Source Serif 4 + Noto Serif SC',family:'RF Source Serif 4',file:'SourceSerif4Variable-Roman.woff2',weight:'200 900',fallback:'noto-serif-sc'},
    {id:'jetbrains-mono',name:'JetBrains Mono + Noto Sans SC',family:'RF JetBrains Mono',file:'JetBrainsMono-Variable.ttf',weight:'100 800',fallback:'noto-sans-sc'}
  ];
  const languages = ['en','fr','ru','es','ja','pt','de','sr','ko'];
  const dictionary = {
    font:['字体','Font','Police','Шрифт','Fuente','フォント','Fonte','Schrift','Фонт','글꼴'],
    original:['默认字体','Default font','Police par défaut','Шрифт по умолчанию','Fuente predeterminada','既定のフォント','Fonte padrão','Standardschrift','Подразумевани фонт','기본 글꼴'],
    import:['导入本地字体','Import a font file','Importer une police','Импортировать шрифт','Importar fuente','フォントを読み込む','Importar fonte','Schrift importieren','Увези фонт','로컬 글꼴 가져오기'],
    remove:['移除本地字体','Remove imported font','Supprimer la police importée','Удалить импортированный шрифт','Eliminar fuente importada','読み込んだフォントを削除','Remover fonte importada','Importierte Schrift entfernen','Уклони увезени фонт','가져온 글꼴 삭제'],
    local:['本地字体','Imported font','Police importée','Импортированный шрифт','Fuente importada','読み込んだフォント','Fonte importada','Importierte Schrift','Увезени фонт','가져온 글꼴'],
    note:['用于界面与题录阅读，不改变 PDF 原文排版。支持 TTF、OTF、WOFF、WOFF2，单文件最多 32 MB。','Applies to the interface and reference text; PDF layout stays unchanged. TTF, OTF, WOFF and WOFF2, up to 32 MB per file.','S’applique à l’interface et aux références, sans modifier les PDF. TTF, OTF, WOFF et WOFF2 ; 32 Mo maximum.','Для интерфейса и библиографических записей; вёрстка PDF не меняется. TTF, OTF, WOFF и WOFF2, до 32 МБ.','Se aplica a la interfaz y las referencias; no cambia los PDF. TTF, OTF, WOFF y WOFF2, hasta 32 MB.','画面と文献情報に適用し、PDF の組版は変更しません。TTF・OTF・WOFF・WOFF2、1 ファイル 32 MB まで。','Aplica-se à interface e às referências, sem alterar PDFs. TTF, OTF, WOFF e WOFF2, até 32 MB.','Für Oberfläche und Literaturangaben; das PDF-Layout bleibt unverändert. TTF, OTF, WOFF und WOFF2, bis 32 MB.','За интерфејс и библиографске записе; PDF распоред се не мења. TTF, OTF, WOFF и WOFF2, до 32 MB.','화면과 서지 정보에 적용하며 PDF 원문 배치는 변경하지 않습니다. TTF, OTF, WOFF, WOFF2, 파일당 최대 32 MB.'],
    privacy:['字体文件仅保存在此浏览器，不会上传；可随时移除。清除网站数据会删除已导入的字体。','Font files stay in this browser and are never uploaded. Remove them at any time; clearing site data also deletes imported fonts.','Les fichiers restent dans ce navigateur, sans envoi. Vous pouvez les supprimer ; l’effacement des données du site les supprime aussi.','Файлы остаются в этом браузере и не загружаются на сервер. Их можно удалить; очистка данных сайта также удалит шрифты.','Los archivos permanecen en este navegador y no se suben. Puede eliminarlos; borrar los datos del sitio también los elimina.','フォントはこのブラウザー内に保存され、送信されません。いつでも削除でき、サイトデータの消去でも削除されます。','Os arquivos ficam neste navegador e não são enviados. Você pode removê-los; limpar os dados do site também os exclui.','Schriftdateien bleiben in diesem Browser und werden nicht hochgeladen. Sie können sie entfernen; das Löschen der Websitedaten entfernt sie ebenfalls.','Датотеке остају у овом прегледачу и не отпремају се. Можете их уклонити; брисање података сајта такође их уклања.','글꼴 파일은 이 브라우저에만 저장되며 업로드되지 않습니다. 언제든 삭제할 수 있고 사이트 데이터를 지워도 삭제됩니다.'],
    loading:['正在加载字体…','Loading font…','Chargement…','Загрузка шрифта…','Cargando fuente…','読み込み中…','Carregando fonte…','Schrift wird geladen…','Учитавање фонта…','글꼴을 불러오는 중…'],
    failed:['无法加载这个字体，请选择其他字体文件。','This font could not be loaded. Choose another font file.','Impossible de charger cette police. Choisissez un autre fichier.','Не удалось загрузить шрифт. Выберите другой файл.','No se pudo cargar esta fuente. Elija otro archivo.','このフォントを読み込めませんでした。別のファイルを選択してください。','Não foi possível carregar esta fonte. Escolha outro arquivo.','Diese Schrift konnte nicht geladen werden. Wählen Sie eine andere Datei.','Фонт није учитан. Изаберите другу датотеку.','글꼴을 불러올 수 없습니다. 다른 파일을 선택하세요.'],
    invalid:['请选择不超过 32 MB 的 TTF、OTF、WOFF 或 WOFF2 字体文件。','Choose a TTF, OTF, WOFF or WOFF2 font file no larger than 32 MB.','Choisissez une police TTF, OTF, WOFF ou WOFF2 de 32 Mo maximum.','Выберите TTF, OTF, WOFF или WOFF2 размером не более 32 МБ.','Elija un archivo TTF, OTF, WOFF o WOFF2 de hasta 32 MB.','32 MB 以下の TTF・OTF・WOFF・WOFF2 を選択してください。','Escolha um arquivo TTF, OTF, WOFF ou WOFF2 de até 32 MB.','Wählen Sie eine TTF-, OTF-, WOFF- oder WOFF2-Datei bis 32 MB.','Изаберите TTF, OTF, WOFF или WOFF2 до 32 MB.','32 MB 이하의 TTF, OTF, WOFF, WOFF2 파일을 선택하세요.'],
    session:['此浏览器无法保存字体；本次会话仍可使用，重新打开后需再次导入。','This browser cannot save the font. It works for this session; import it again after reopening.','Ce navigateur ne peut pas conserver la police. Elle reste disponible pour cette session ; réimportez-la ensuite.','Браузер не может сохранить шрифт. Он доступен до закрытия; затем импортируйте его снова.','Este navegador no puede guardar la fuente. Se usará en esta sesión; impórtela al volver a abrir.','このブラウザーでは保存できません。このセッションでは使用できますが、次回は再度読み込んでください。','Este navegador não pode salvar a fonte. Ela funciona nesta sessão; importe-a novamente ao reabrir.','Dieser Browser kann die Schrift nicht speichern. Sie gilt für diese Sitzung; importieren Sie sie beim nächsten Öffnen erneut.','Прегледач не може да сачува фонт. Доступан је у овој сесији; касније га поново увезите.','브라우저가 글꼴을 저장할 수 없습니다. 현재 세션에서는 사용 가능하며 다시 열면 재가져오기가 필요합니다.'],
    saved:['已保存在此浏览器','Saved in this browser','Conservée dans ce navigateur','Сохранён в этом браузере','Guardada en este navegador','このブラウザーに保存済み','Salva neste navegador','In diesem Browser gespeichert','Сачуван у овом прегледачу','이 브라우저에 저장됨'],
    preferenceSession:['字体选择未能保存；本次会话已生效，重新打开后请再次选择。','The font choice could not be saved. It applies for this session; choose it again after reopening.','Le choix de police n’a pas été enregistré. Il s’applique à cette session ; sélectionnez-le à nouveau ensuite.','Выбор шрифта не сохранён. Он действует в этой сессии; после открытия выберите его снова.','No se guardó la selección. Se aplica en esta sesión; vuelva a elegirla al abrir de nuevo.','フォントの選択を保存できませんでした。このセッションでは有効です。次回は再度選択してください。','A escolha não foi salva. Ela vale nesta sessão; selecione novamente ao reabrir.','Die Schriftauswahl konnte nicht gespeichert werden. Sie gilt für diese Sitzung; wählen Sie sie beim nächsten Öffnen erneut.','Избор фонта није сачуван. Важи у овој сесији; изаберите га поново при отварању.','글꼴 선택을 저장하지 못했습니다. 현재 세션에는 적용되며 다시 열면 재선택이 필요합니다.'],
    removed:['已移除本地字体','Imported font removed','Police importée supprimée','Импортированный шрифт удалён','Fuente importada eliminada','読み込んだフォントを削除しました','Fonte importada removida','Importierte Schrift entfernt','Увезени фонт је уклоњен','가져온 글꼴 삭제됨'],
    deleteFailed:['无法删除此浏览器保存的字体，请重试或清除此网站的浏览数据。','The saved font could not be deleted. Retry or clear this site’s browser data.','La police n’a pas pu être supprimée. Réessayez ou effacez les données de ce site.','Не удалось удалить сохранённый шрифт. Повторите попытку или очистите данные сайта.','No se pudo eliminar la fuente guardada. Reintente o borre los datos del sitio.','保存済みフォントを削除できませんでした。再試行するか、このサイトのデータを消去してください。','Não foi possível excluir a fonte salva. Tente novamente ou limpe os dados deste site.','Die gespeicherte Schrift konnte nicht gelöscht werden. Versuchen Sie es erneut oder löschen Sie die Websitedaten.','Сачувани фонт није обрисан. Покушајте поново или обришите податке сајта.','저장된 글꼴을 삭제하지 못했습니다. 다시 시도하거나 이 사이트의 데이터를 지우세요.']
  };
  let block, select, picker, remove, status, localRecord = null, localFace = null;
  let selected = 'default', requestId = 0, localRevision = 0, busy = false, dbPromise = null;
  let noticeKey = '', localSaved = false, hasStoredLocal = false, scheduled = 0;
  const faces = new Map();
  function tr(key) { const lang = window.RFLang?.get?.() || root.lang || 'zh-CN'; const index = languages.indexOf(lang); return dictionary[key][index < 0 ? 0 : index + 1]; }
  function element(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; }
  function database() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve,reject) => {
      if (!window.indexedDB) { reject(new Error('storage unavailable')); return; }
      const open = indexedDB.open(databaseName,1);
      let settled = false;
      const timer = setTimeout(() => { settled = true; reject(new Error('storage blocked')); },5000);
      open.onupgradeneeded = () => { if (!open.result.objectStoreNames.contains('fonts')) open.result.createObjectStore('fonts'); };
      open.onerror = () => { clearTimeout(timer); settled = true; reject(open.error); };
      open.onsuccess = () => { clearTimeout(timer); if (settled) { open.result.close(); return; } settled = true; open.result.onversionchange = () => { open.result.close(); dbPromise = null; }; resolve(open.result); };
    }).catch(error => { dbPromise = null; throw error; });
    return dbPromise;
  }
  async function storeRecord(action, record) {
    const db = await database();
    return new Promise((resolve,reject) => {
      const tx = db.transaction('fonts', action === 'get' ? 'readonly' : 'readwrite'), store = tx.objectStore('fonts');
      let value;
      const request = action === 'get' ? store.get('local') : action === 'delete' ? store.delete('local') : store.put(record,'local');
      request.onsuccess = () => { value = request.result; };
      tx.oncomplete = () => resolve(value);
      tx.onerror = tx.onabort = () => reject(tx.error || new Error('storage failed'));
    });
  }
  function sync() {
    if (!block) return;
    for (const n of block.querySelectorAll('[data-rff-text]')) n.textContent = tr(n.dataset.rffText);
    const old = select.querySelector('[value="local"]');
    if (localRecord) {
      const option = old || element('option'); option.value = 'local'; option.textContent = tr('local') + ' — ' + localRecord.name;
      if (!old) select.append(option);
    } else old?.remove();
    select.value = selected; picker.disabled = busy; remove.disabled = busy || !localRecord;
    remove.hidden = !localRecord;
    const importButton = block.querySelector('.rff-import'); importButton.disabled = busy;
    status.textContent = noticeKey ? tr(noticeKey) : localRecord ? tr(localSaved ? 'saved' : 'session') + ': ' + localRecord.name : '';
    status.hidden = !status.textContent;
  }
  function mount() {
    const gallery = document.getElementById('rfThemeSuites'); if (!gallery) return;
    if (!block) {
      block = element('section','rff-settings'); block.id = 'rfTypographySettings'; block.setAttribute('aria-labelledby','rfTypographyLabel');
      const row = element('div','rff-controls'), field = element('div','rff-field'), label = element('label','rff-label');
      label.id = 'rfTypographyLabel'; label.htmlFor = 'rfFontSelect'; label.dataset.rffText = 'font';
      select = element('select','input'); select.id = 'rfFontSelect';
      const original = element('option'); original.value = 'default'; original.dataset.rffText = 'original'; select.append(original);
      for (const font of fonts) { const option = element('option','',font.name); option.value = font.id; select.append(option); }
      field.append(label,select);
      const actions = element('div','rff-actions'), importButton = element('button','btn b-out rff-import'); importButton.type = 'button'; importButton.dataset.rffText = 'import';
      remove = element('button','btn b-out'); remove.id = 'rfFontRemove'; remove.type = 'button'; remove.dataset.rffText = 'remove';
      picker = element('input','rff-file'); picker.id = 'rfFontFile'; picker.type = 'file'; picker.accept = '.ttf,.otf,.woff,.woff2'; picker.tabIndex = -1; picker.hidden = true;
      const note = element('p','hint rff-note'); note.dataset.rffText = 'note'; note.id = 'rfFontNote'; select.setAttribute('aria-describedby',note.id);
      const privacy = element('p','hint rff-privacy'); privacy.dataset.rffText = 'privacy';
      const preview = element('p','rff-preview','研究始于清晰的阅读。 Research begins with careful reading. 0123456789'); preview.lang = 'zh-CN';
      status = element('p','hint rff-status'); status.setAttribute('role','status'); status.setAttribute('aria-live','polite');
      actions.append(importButton,remove,picker); row.append(field,actions); block.append(row,note,preview,privacy,status);
      select.addEventListener('change',() => { void applyChoice(select.value); });
      importButton.addEventListener('click',() => picker.click());
      picker.addEventListener('change',() => { const file = picker.files?.[0]; if (file) void importFile(file); });
      remove.addEventListener('click',() => { void removeLocal(); });
    }
    if (block.parentElement !== gallery) {
      const preferences = gallery.querySelector('.rft-native-preferences');
      if (preferences) preferences.after(block); else gallery.append(block);
    }
    sync();
  }
  async function bundledFace(id) {
    if (faces.has(id)) return faces.get(id);
    const font = fonts.find(item => item.id === id); if (!font) throw new Error('unknown font');
    const loading = new FontFace(font.family, 'url("./font-assets/' + font.file + '")', {weight:font.weight,display:'swap'}).load()
      .then(face => { document.fonts.add(face); return face; }).catch(error => { faces.delete(id); throw error; });
    faces.set(id,loading); return loading;
  }
  function saveChoice(id) {
    try { if (id === 'default') localStorage.removeItem(choiceKey); else localStorage.setItem(choiceKey,id); return true; } catch (_) { return false; }
  }
  function commitChoice(id, families, persist) {
    selected = id;
    if (id === 'default') { root.removeAttribute('data-rf-font'); root.style.removeProperty('--rff-selected-family'); }
    else { root.dataset.rfFont = id; root.style.setProperty('--rff-selected-family',families + ', sans-serif'); }
    if (persist && !saveChoice(id)) noticeKey = 'preferenceSession';
    sync(); document.dispatchEvent(new CustomEvent('reviewflow:font-changed',{detail:{id}}));
  }
  async function applyChoice(id, persist = true) {
    const job = ++requestId; noticeKey = id === 'default' ? '' : 'loading'; sync();
    try {
      if (id === 'default') { commitChoice(id,'',persist); return; }
      let family;
      if (id === 'local') {
        if (!localRecord) throw new Error('missing local font');
        if (!localFace) { const face = await new FontFace('RF Imported Font',localRecord.bytes,{display:'swap'}).load(); if (job !== requestId) return; document.fonts.add(face); localFace = face; }
        family = '"RF Imported Font"';
      } else {
        const font = fonts.find(item => item.id === id); if (!font) throw new Error('unknown font');
        await bundledFace(id); if (font.fallback) await bundledFace(font.fallback);
        family = '"' + font.family + '"' + (font.fallback ? ', "' + fonts.find(item => item.id === font.fallback).family + '"' : '');
      }
      if (job !== requestId) return;
      noticeKey = ''; commitChoice(id,family,persist);
    } catch (_) { if (job === requestId) { noticeKey = 'failed'; sync(); } }
  }
  async function importFile(file) {
    if (busy) return;
    if (!/\.(ttf|otf|woff2?)$/i.test(file.name) || !file.size || file.size > limit) { noticeKey = 'invalid'; picker.value = ''; sync(); return; }
    const job = ++requestId; ++localRevision; busy = true; noticeKey = 'loading'; sync();
    try {
      const bytes = await file.arrayBuffer(), face = await new FontFace('RF Imported Font',bytes,{display:'swap'}).load();
      if (job !== requestId) return;
      const record = {name:file.name.slice(0,240),bytes};
      let saved = true; try { await storeRecord('put',record); hasStoredLocal = true; } catch (_) { saved = false; }
      if (localFace) document.fonts.delete(localFace);
      document.fonts.add(face); localFace = face; localRecord = record; localSaved = saved;
      if (job === requestId) { noticeKey = saved ? '' : 'session'; commitChoice('local','"RF Imported Font"',saved); }
      else sync();
    } catch (_) { if (job === requestId) { noticeKey = 'failed'; sync(); } }
    finally { busy = false; picker.value = ''; sync(); }
  }
  async function removeLocal() {
    if (busy || !localRecord) return;
    ++requestId; ++localRevision; busy = true; sync();
    try {
      if (hasStoredLocal) await storeRecord('delete');
      if (localFace) document.fonts.delete(localFace);
      localRecord = localFace = null; localSaved = hasStoredLocal = false; noticeKey = 'removed';
      if (selected === 'local') commitChoice('default','',true);
    } catch (_) { noticeKey = 'deleteFailed'; }
    finally { busy = false; sync(); }
  }
  async function restoreSaved() {
    let wanted = 'default'; try { wanted = localStorage.getItem(choiceKey) || 'default'; } catch (_) {}
    const revision = localRevision, initialRequest = requestId;
    try {
      const record = await storeRecord('get');
      if (record) hasStoredLocal = true;
      if (localRevision === revision && record && typeof record.name === 'string' && record.bytes instanceof ArrayBuffer && record.bytes.byteLength > 0 && record.bytes.byteLength <= limit) { localRecord = record; localSaved = hasStoredLocal = true; }
    } catch (_) { if (wanted === 'local') noticeKey = 'session'; }
    if (requestId === initialRequest) {
      if (wanted === 'local' && !localRecord) { saveChoice('default'); wanted = 'default'; }
      if (wanted === 'default' || wanted === 'local' || fonts.some(font => font.id === wanted)) await applyChoice(wanted,false);
    }
    sync();
  }
  function schedule() { if (!scheduled) scheduled = requestAnimationFrame(() => { scheduled = 0; mount(); }); }
  const observer = new MutationObserver(() => { if (!block?.isConnected && document.getElementById('rfThemeSuites')) schedule(); });
  observer.observe(document.body,{childList:true,subtree:true});
  document.addEventListener('reviewflow:language-changed',schedule);
  document.addEventListener('reviewflow:layout-preview-mode',schedule);
  window.RFTypography = Object.freeze({select:applyChoice,refresh:schedule,fonts,get selected(){return selected;},get ready(){return !!block?.isConnected;}});
  mount(); void restoreSaved();
})();
