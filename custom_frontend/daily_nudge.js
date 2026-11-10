/* Personal screening goals and opt-in calendar reminders. */
(function () {
  'use strict';
  const anchor = document.querySelector('.page[data-p="screen"] .scr-status');
  if (!anchor) return;
  const style = el('style', {text: [
    '.rf-daily-trigger,.rf-daily-metrics{display:flex;gap:9px;align-items:center}',
    '.rf-daily-trigger[hidden]{display:none}',
    '.rf-daily-metrics{flex-wrap:wrap}',
    '.rf-daily-ring{display:inline-grid;place-items:center;flex:none;width:24px;height:24px;border-radius:50%;background:conic-gradient(var(--primary) var(--daily-progress),var(--border) 0)}',
    '.rf-daily-ring::after{content:"";width:calc(100% - 6px);height:calc(100% - 6px);border-radius:50%;background:var(--bg)}',
    '.rf-daily-metrics .rf-daily-ring{width:44px;height:44px}',
    '.rf-daily-days{display:flex;flex-wrap:wrap;gap:5px;list-style:none;padding:0;margin:6px 0 12px}',
    '.rf-daily-days li{width:12px;height:12px;border:1px solid var(--primary);border-radius:3px}',
    '.rf-daily-days li.met{background:var(--primary)}.rf-daily-days li.in-progress{border-style:dashed;border-width:2px}',
    '.rf-daily-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(200px,100%),1fr));gap:8px;border:0;padding:0;margin:0}',
    '.rf-daily-fields label{display:flex;flex-direction:column;gap:4px;min-width:0}.rf-daily-fields label:has(input[type=checkbox]){flex-direction:row;align-items:center}',
    '#rfDailyNudgeDialog section{margin:14px 0;padding:10px;border:1px solid var(--border);border-radius:10px}',
    '#rfDailyNudgeDialog h3,#rfDailyNudgeDialog h4{margin:0 0 8px}',
  ].join('\n')});
  document.head.append(style);

  const launchText = el('span', {});
  const launch = el('button', {type: 'button', class: 'btn b-out rf-daily-trigger', hidden: ''}, launchText);
  anchor.append(launch);
  const heading = el('h3', {id: 'rfDailyNudgeHeading'});
  const close = el('button', {type: 'button', class: 'btn b-out', text: T('关闭')});
  const errorLine = el('p', {class: 'hint', role: 'status'});
  const memberBox = el('div', {class: 'row'});
  const statusBox = el('section', {});
  const settingsBox = el('section', {});
  const tokenBox = el('section', {});
  const channelBox = el('section', {});
  const dialog = el('dialog', {id: 'rfDailyNudgeDialog', class: 'card', 'aria-labelledby': heading.id,
    style: 'width:min(720px,calc(100% - 24px));max-height:calc(100dvh - 24px);overflow:auto;margin:auto'},
    el('div', {class: 'row', style: 'justify-content:space-between'}, heading, close),
    errorLine, memberBox, statusBox, settingsBox, tokenBox, channelBox);
  document.body.append(dialog);
  dialog.addEventListener('keydown', event => event.stopPropagation());

  let scope = 0;
  let ownTicket = 0;
  let dialogTicket = 0;
  let own = null;
  let selectedMember = '';
  let channelsAllowed = false;

  function msg(key, ...args) { return T('daily_nudge.' + key, ...args); }
  function p(text) { return el('p', {class: 'hint', text}); }
  function button(text, onclick, kind = 'b-out') { return el('button', {type: 'button', class: 'btn ' + kind, text, onclick}); }
  function base() { return taskApi() + '/screening'; }
  function active(ticket) { return dialog.open && ticket === dialogTicket; }
  function ring(status) {
    const ratio = status.target_set ? Math.min(100, 100 * status.today_screened / status.daily_target) : 0;
    return el('span', {class: 'rf-daily-ring', style: '--daily-progress:' + ratio + '%', 'aria-hidden': 'true'});
  }
  function updateBadge() {
    launch.hidden = !S.task;
    launch.setAttribute('aria-label', msg('title'));
    if (own?.available && own.settings_available && own.target_set) {
      launchText.textContent = msg('today.progress', {screened: own.today_screened, target: own.daily_target});
      launch.replaceChildren(ring(own), launchText);
    } else {
      launchText.textContent = msg('title');
      launch.replaceChildren(launchText);
    }
  }
  function renderStatus(status, member) {
    statusBox.replaceChildren();
    if (member) statusBox.append(el('h4', {text: msg('member_readonly', member)}));
    if (!status.available) { statusBox.append(p(msg('status_unavailable'))); return; }
    if (!status.settings_available) statusBox.append(p(msg('storage_unavailable')));
    const ongoing = !status.today_met && status.streak > 0;
    const today = status.target_set ? msg('today.progress', {screened: status.today_screened, target: status.daily_target}) : msg('target_unset');
    statusBox.append(el('div', {class: 'rf-daily-metrics'}, ring(status), el('b', {text: today}),
      el('span', {class: 'chip' + (status.streak > 0 ? ' c-inc' : ''), text: msg('streak.label', status.streak)}),
      el('span', {class: 'chip', text: msg('streak.longest', status.longest_streak)})));
    if (ongoing) statusBox.append(p(msg('streak.in_progress')));
    const days = el('ul', {class: 'rf-daily-days', 'aria-label': msg('last14.label')});
    status.last_14_days.forEach((day, index) => {
      const text = msg('day_count', day.date, day.screened);
      const className = day.met ? 'met' : ongoing && index === status.last_14_days.length - 1 ? 'in-progress' : '';
      days.append(el('li', {class: className, title: text, 'aria-label': text}));
    });
    statusBox.append(p(msg('last14.label')), days, p(msg('streak.disclosure')));
  }
  async function refreshStatus() {
    if (!S.task || S.page !== 'screen') return;
    const ticket = ++ownTicket;
    const task = scope;
    try {
      const status = await api(base() + '/daily-status');
      if (ticket !== ownTicket || task !== scope) return;
      own = status;
      updateBadge();
      if (dialog.open && !selectedMember) renderStatus(own, '');
    } catch (_) {
      if (ticket !== ownTicket || task !== scope) return;
      own = null;
      updateBadge();
    }
  }

  function input(label, control) { return el('label', {}, el('span', {text: label}), control); }
  function txt(value, attrs = {}) { return el('input', {class: 'input', value: value ?? '', ...attrs}); }
  function checked(value) {
    const node = el('input', {type: 'checkbox'});
    node.checked = value === true;
    return node;
  }

  function renderSettings(status, ticket) {
    settingsBox.replaceChildren(el('h4', {text: msg('settings')}));
    const target = txt(status.daily_target, {type: 'number', min: 0, max: 10000, step: 1, required: ''});
    const remindTime = txt(status.remind_time, {type: 'time'});
    const remindEnabled = checked(status.remind_enabled);
    const deadline = txt(status.deadline, {type: 'date'});
    const timezone = txt(status.timezone, {placeholder: 'Asia/Shanghai'});
    const controls = el('fieldset', {class: 'rf-daily-fields'},
      input(msg('target_label'), target), input(msg('remind_time_label'), remindTime),
      input(msg('deadline_label'), deadline), input(msg('timezone_label'), timezone),
      input(msg('remind_enabled_label'), remindEnabled));
    const form = el('form', {}, controls, p(msg('timezone_help')));
    const line = el('p', {class: 'hint', role: 'status'});
    const use = button(msg('suggestion.use'), () => {
      if (suggestion !== null) target.value = String(suggestion);
    });
    use.hidden = true;
    let suggestion = null;
    let request = 0;
    deadline.addEventListener('input', () => { request++; use.hidden = true; line.textContent = ''; });
    const preview = button(msg('suggestion.get'), async () => {
      const sequence = ++request;
      use.hidden = true;
      suggestion = null;
      line.textContent = '';
      // An explicitly cleared draft date must not fall back to the stored date.
      if (!deadline.value && status.deadline) {
        line.textContent = msg('suggestion.deadline_required');
        return;
      }
      const query = deadline.value ? '?deadline=' + encodeURIComponent(deadline.value) : '';
      try {
        const result = await api(base() + '/daily-status/suggestion' + query);
        if (!active(ticket) || sequence !== request) return;
        if (!result.available) line.textContent = msg('status_unavailable');
        else if (!result.suggestion_available) line.textContent = msg('suggestion.deadline_required');
        else {
          suggestion = result.suggested_target;
          line.textContent = msg('suggestion.label', suggestion, result.remaining_unscreened, result.remaining_days);
          use.hidden = false;
        }
      } catch (error) { if (active(ticket) && sequence === request) line.textContent = error.message; }
    });
    const save = el('button', {type: 'submit', class: 'btn b-inc', text: msg('save_target')});
    form.append(el('div', {class: 'row'}, preview, use, save), line);
    settingsBox.append(form);
    const disabled = !status.settings_available;
    controls.disabled = save.disabled = preview.disabled = disabled;
    if (disabled) line.textContent = msg('storage_unavailable');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (controls.disabled) return;
      const payload = {
        daily_target: Number(target.value), remind_time: remindTime.value, remind_enabled: remindEnabled.checked,
        deadline: deadline.value, timezone: timezone.value,
      };
      controls.disabled = save.disabled = preview.disabled = use.disabled = true;
      request++;
      try {
        const result = await api(base() + '/daily-target', {method: 'PUT', body: payload});
        if (!active(ticket)) return;
        status = {...status, ...result.settings};
        target.value = status.daily_target;
        remindTime.value = status.remind_time;
        deadline.value = status.deadline || '';
        timezone.value = status.timezone || '';
        remindEnabled.checked = status.remind_enabled;
        use.hidden = true;
        line.textContent = msg('saved');
        refreshStatus();
      } catch (error) {
        if (active(ticket)) line.textContent = error.message;
      } finally { controls.disabled = save.disabled = preview.disabled = use.disabled = false; }
    });
  }

  function renderTokens(ticket) {
    tokenBox.replaceChildren(el('h4', {text: msg('ics.title')}), p(msg('ics.disclosure')), p(msg('ics.instructions')));
    const message = el('p', {class: 'hint', role: 'status'});
    const secret = el('div', {});
    const list = el('ul', {});
    let listRequest = 0;
    const generate = button(msg('ics.generate'), async () => {
      secret.replaceChildren();
      generate.disabled = true;
      message.textContent = '';
      try {
        const result = await api(base() + '/ics-tokens', {method: 'POST'});
        if (!active(ticket)) return;
        const value = location.origin + result.feed_path;
        const field = txt(value, {readonly: '', translate: 'no', 'aria-label': msg('ics.url_label')});
        const copy = button(msg('ics.copy_url'), async () => {
          try {
            await navigator.clipboard.writeText(value);
            if (active(ticket)) message.textContent = msg('ics.copied');
          } catch (_) {
            field.focus();
            field.select();
            message.textContent = msg('ics.copy_manual');
          }
        });
        secret.replaceChildren(p(msg('ics.once')), el('div', {class: 'row'}, field, copy));
        await loadList();
      } catch (error) {
        if (active(ticket)) message.textContent = error.message;
      } finally { if (active(ticket)) generate.disabled = false; }
    });
    async function revoke(id) {
      await api(base() + '/ics-tokens/' + encodeURIComponent(id), {method: 'DELETE'});
      if (!active(ticket)) return;
      secret.replaceChildren();
      message.textContent = msg('ics.revoked');
      await loadList();
    }
    async function loadList() {
      const request = ++listRequest;
      try {
        const data = await api(base() + '/ics-tokens');
        if (!active(ticket) || request !== listRequest) return;
        list.replaceChildren();
        if (!data.available) {
          message.textContent = msg('storage_unavailable');
          return;
        }
        if (!data.tokens.length) list.append(el('li', {text: msg('ics.empty')}));
        for (const item of data.tokens) {
          const row = el('li', {}, el('code', {text: item.token_id, translate: 'no'}), ' ',
            el('time', {text: new Date(item.created_at).toLocaleString(document.documentElement.lang), translate: 'no'}), ' ',
            el('span', {text: msg(item.revoked_at ? 'ics.revoked_label' : 'ics.valid_label')}));
          if (!item.revoked_at) {
            const btn = button(msg('ics.revoke'), async () => {
              btn.disabled = true;
              try { await revoke(item.token_id); }
              catch (error) { if (active(ticket)) message.textContent = error.message; }
              finally { btn.disabled = false; }
            });
            row.append(' ', btn);
          }
          list.append(row);
        }
      } catch (error) {
        if (active(ticket) && request === listRequest) message.textContent = error.message;
      }
    }
    tokenBox.append(generate, secret, list, message);
    if (channelsAllowed) {
      const field = txt('', {maxlength: 24, placeholder: msg('ics.member_token'), 'aria-label': msg('ics.member_token')});
      const revokeMember = button(msg('ics.revoke_member'), async () => {
        const id = field.value.trim();
        if (!id) { field.focus(); return; }
        revokeMember.disabled = true;
        try { await revoke(id); field.value = ''; }
        catch (error) { if (active(ticket)) message.textContent = error.message; }
        finally { revokeMember.disabled = false; }
      });
      tokenBox.append(el('div', {class: 'row'}, field, revokeMember));
    }
    loadList();
  }

  const channelFields = {
    webhook: ['url', 'auth_token_env'],
    serverchan: ['send_key_env'],
    apprise: ['url_env'],
    smtp: ['host', 'port', 'from_addr', 'to_addr', 'username', 'password_env', 'use_tls'],
  };
  const technicalNames = {webhook: 'Webhook', serverchan: 'ServerChan', apprise: 'Apprise', smtp: 'SMTP'};
  function deliveryMessage(result) {
    if (result.ok && result.status === 'sent') return msg('notify.sent');
    if (result.status === 'channel_unavailable' && result.reason === 'apprise_missing') return msg('notify.apprise_missing');
    if (result.status === 'credential_missing') return msg('notify.credential_missing') +
      (result.reason === 'env_name_forbidden' ? ' ' + msg('notify.env_prefix') : '');
    const keys = {
      disabled: 'channel_disabled', private_host: 'notify.bad_host', invalid_host: 'notify.bad_host',
      unresolvable_host: 'notify.bad_host', redirect_refused: 'notify.redirect_refused',
      timeout: 'notify.network', unreachable: 'notify.network', http_error: 'notify.upstream',
      upstream_rejected: 'notify.upstream', channel_invalid: 'notify.invalid',
    };
    return msg(keys[result.status] || 'notify.channel_error');
  }
  function renderChannels(config, ticket) {
    channelBox.replaceChildren(el('h4', {text: msg('notify.title')}),
      p(msg('outbound.disclosure')), p(msg('outbound.credentials')), p(msg('notify.env_prefix')));
    if (!config.available) { channelBox.append(p(msg('storage_unavailable'))); return; }
    const enabled = checked(config.enabled);
    const types = el('select', {class: 'input'}, Object.entries(technicalNames).map(([type, label]) =>
      el('option', {value: type, text: label, translate: 'no'})));
    types.value = config.type;
    const channelDisclosure = p('');
    const inputs = {};
    const fieldLabels = {};
    const fields = el('fieldset', {class: 'rf-daily-fields'},
      input(msg('notify.enabled'), enabled), input(msg('notify.type'), types));
    for (const field of Object.values(channelFields).flat()) {
      if (field === 'use_tls') inputs[field] = checked(config[field]);
      else if (field === 'port') inputs[field] = txt(config[field], {type: 'number', min: 1, max: 65535, step: 1});
      else inputs[field] = txt(config[field], field.endsWith('_env') ? {placeholder: 'REVIEWFLOW_NOTIFY_…'} : {});
      fieldLabels[field] = input(msg('notify.' + field), inputs[field]);
      fields.append(fieldLabels[field]);
    }
    function syncType() {
      const type = types.value;
      for (const [field, label] of Object.entries(fieldLabels)) {
        const visible = channelFields[type].includes(field);
        label.hidden = !visible;
        label.style.display = visible ? '' : 'none';
        inputs[field].disabled = !visible;
      }
      channelDisclosure.textContent = msg('outbound.disclosure_' + type) +
        (type === 'webhook' ? ' ' + msg('outbound.webhook_public_only') : '');
    }
    syncType();
    types.addEventListener('change', syncType);

    const line = el('p', {class: 'hint', role: 'status'});
    const save = el('button', {type: 'submit', class: 'btn b-inc', text: msg('notify.save')});
    const test = button(msg('notify.send_test'), async () => {
      test.disabled = true;
      save.disabled = true;
      try {
        const body = selectedMember ? {screener: selectedMember} : {};
        const result = await api(base() + '/notify/test', {method: 'POST', body});
        if (active(ticket)) line.textContent = deliveryMessage(result);
      } catch (error) { if (active(ticket)) line.textContent = error.message; }
      finally { test.disabled = !config.enabled; save.disabled = false; }
    });
    test.disabled = !config.enabled;
    const form = el('form', {}, channelDisclosure, fields, el('div', {class: 'row'}, save, test), line);
    channelBox.append(form);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (fields.disabled || save.disabled) return;
      const body = {enabled: enabled.checked, type: types.value};
      for (const [field, node] of Object.entries(inputs)) {
        body[field] = field === 'port' ? Number(node.value || 587) :
          field === 'use_tls' ? node.checked : node.value;
      }
      fields.disabled = save.disabled = test.disabled = true;
      try {
        const result = await api(base() + '/notify/channels', {method: 'PUT', body});
        if (!active(ticket)) return;
        config = result.channels;
        line.textContent = msg('saved');
      } catch (error) { if (active(ticket)) line.textContent = error.message; }
      finally { fields.disabled = save.disabled = false; test.disabled = !config.enabled; }
    });
  }

  function memberControls(members, ticket) {
    if (!members.length) return;
    const list = el('select', {class: 'input'}, el('option', {value: '', text: msg('my_status')}),
      members.map(member => el('option', {value: member.username, text: member.username, translate: 'no'})));
    const line = el('p', {class: 'hint', role: 'status'});
    memberBox.replaceChildren(input(msg('member_select'), list), line);
    let request = 0;
    list.addEventListener('change', async () => {
      selectedMember = list.value;
      settingsBox.hidden = !!selectedMember;
      const sequence = ++request;
      statusBox.replaceChildren();
      line.textContent = '';
      if (!selectedMember) {
        if (own) renderStatus(own, '');
        return;
      }
      const query = '?screener=' + encodeURIComponent(selectedMember);
      const valid = () => active(ticket) && sequence === request;
      try {
        const [status, suggestion] = await Promise.all([
          api(base() + '/daily-status' + query), api(base() + '/daily-status/suggestion' + query),
        ]);
        if (!valid()) return;
        renderStatus(status, selectedMember);
        if (suggestion.available) line.textContent = suggestion.suggestion_available
          ? msg('suggestion.label', suggestion.suggested_target, suggestion.remaining_unscreened, suggestion.remaining_days)
          : msg('suggestion.deadline_required');
      } catch (error) { if (valid()) line.textContent = error.message; }
    });
  }
  async function open() {
    if (!S.task || dialog.open) return;
    const ticket = ++dialogTicket;
    const ownRequest = ++ownTicket;
    selectedMember = '';
    settingsBox.hidden = false;
    channelsAllowed = false;
    heading.textContent = msg('title');
    for (const box of [memberBox, statusBox, settingsBox, tokenBox, channelBox]) box.replaceChildren();
    channelBox.hidden = true;
    errorLine.textContent = T('Loading current saved work…');
    dialog.showModal();
    const [status, policy] = await Promise.allSettled([
      api(base() + '/daily-status'), api(taskApi() + '/workflow-settings'),
    ]);
    if (!active(ticket)) return;
    channelsAllowed = policy.status === 'fulfilled' && policy.value.can_manage === true;
    if (status.status === 'fulfilled') {
      errorLine.textContent = '';
      if (ownTicket === ownRequest || !own) own = status.value;
      updateBadge();
      renderStatus(own, '');
      renderSettings(own, ticket);
    } else errorLine.textContent = status.reason.message;
    renderTokens(ticket);
    if (!channelsAllowed) return;
    channelBox.hidden = false;
    channelBox.replaceChildren(p(T('Loading current saved work…')));
    if (runtimeMode === 'server') api(taskApi() + '/team-progress').then(team => {
      if (active(ticket) && team.can_manage) memberControls(team.members.filter(member => member.username !== S.user?.username), ticket);
    }).catch(() => {});
    try {
      const channels = await api(base() + '/notify/channels');
      if (active(ticket)) renderChannels(channels, ticket);
    } catch (error) {
      if (active(ticket)) channelBox.replaceChildren(p(error.message));
    }
  }
  launch.addEventListener('click', open);
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    dialogTicket++;
    for (const box of [memberBox, statusBox, settingsBox, tokenBox, channelBox]) box.replaceChildren();
    errorLine.textContent = '';
    selectedMember = '';
  });
  document.addEventListener('reviewflow:workspace-changed', event => {
    if (event.detail === 'screen') refreshStatus();
    else if (dialog.open) dialog.close();
  });
  document.addEventListener('reviewflow:task-context-changed', () => {
    scope++;
    own = null;
    updateBadge();
    if (dialog.open) dialog.close();
  });
  document.addEventListener('reviewflow:language-changed', () => {
    updateBadge();
    if (dialog.open && own && !selectedMember) renderStatus(own, '');
  });
  updateBadge();
  window.RFDailyNudge = Object.freeze({refreshStatus});
})();
