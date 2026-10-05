(function(){
  'use strict';
  const notice = document.getElementById('rf-update-notice');
  const checkButton = document.getElementById('rf-update-check');
  let latest = null;

  function showDialog(data) {
    const backdrop = document.createElement('div');
    backdrop.className = 'rf-update-backdrop';
    const dialog = document.createElement('section');
    dialog.className = 'rf-update-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', 'rf-update-title');
    const heading = document.createElement('h2');
    heading.id = 'rf-update-title'; heading.textContent = T('发现新版本');
    const version = document.createElement('p');
    version.className = 'rf-update-version';
    version.textContent = T('当前版本 {0} · 最新版本 {1}', data.current_version, data.latest_version);
    const description = document.createElement('p');
    description.textContent = T('此更新将从 GitHub 下载适用于本机系统的安装包。下载完成后，请退出 ReviewFlow 并运行安装包完成更新。');
    const notesLabel = document.createElement('strong'); notesLabel.textContent = T('更新说明');
    const notes = document.createElement('div');
    notes.className = 'rf-update-notes';
    notes.textContent = data.release_notes || data.release_name || '';
    const actions = document.createElement('div'); actions.className = 'rf-update-actions';
    const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = T('取消');
    const download = document.createElement('button'); download.type = 'button'; download.className = 'primary';
    download.textContent = T('下载更新包');
    cancel.addEventListener('click', close);
    download.addEventListener('click', () => {
      const url = data.asset_url || data.release_url;
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
      close();
    });
    actions.append(cancel, download);
    dialog.append(heading, version, description, notesLabel, notes, actions);
    backdrop.append(dialog);
    backdrop.addEventListener('click', event => { if (event.target === backdrop) close(); });
    document.addEventListener('keydown', onKey);
    document.body.append(backdrop);
    cancel.focus();
    function close() { document.removeEventListener('keydown', onKey); backdrop.remove(); }
    function onKey(event) { if (event.key === 'Escape') close(); }
  }

  async function check({quiet = false} = {}) {
    const button = quiet ? notice : checkButton;
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    try {
      const result = await api('/api/app-update/check');
      if (!result.supported) {
        if (!quiet) alert(T('服务器部署不能从应用内更新，请联系管理员。'));
        return;
      }
      latest = result;
      if (result.has_update) {
        notice.hidden = false;
        notice.querySelector('span').textContent = T('发现新版本 · {0}', result.latest_version);
        notice.title = T('发现新版本');
        notice.setAttribute('aria-label', T('发现新版本'));
        if (!quiet) showDialog(result);
      } else if (!quiet) alert(T('当前已是最新版本'));
    } catch (_) {
      if (!quiet) alert(T('暂时无法检查更新，请稍后重试。'));
    } finally {
      if (button) { button.disabled = false; button.removeAttribute('aria-busy'); }
    }
  }

  notice.addEventListener('click', () => { if (latest?.has_update) showDialog(latest); else check(); });
  checkButton.addEventListener('click', () => check());
  check({quiet:true});
})();
