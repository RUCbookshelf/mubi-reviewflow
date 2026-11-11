const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const html = readFileSync(path.join(__dirname, '../../custom_frontend/index.html'), 'utf8');

test('authentication card contains the remember control and inactive reset fields', () => {
  const form = html.match(/<form id="authForm">([\s\S]*?)<\/form>/)[1];
  assert.match(form, /id="authPass"[\s\S]*id="authRemember"[\s\S]*id="authErr"/);
  assert.match(html, /<button[^>]*class="text-button"[^>]*id="authForgot"[^>]*type="button"/);
  assert.match(form, /<fieldset[^>]*id="authResetFields"[^>]*hidden[^>]*disabled/);
  for (const id of ['authResetUser', 'authResetCode', 'authResetPass']) {
    assert.match(form, new RegExp(`<label[^>]*for="${id}"`));
    assert.match(form, new RegExp(`<input[^>]*id="${id}"[^>]*required`));
  }
});

test('auth vocabulary has ten translations including the exact backend error', () => {
  const keys = [
    '记住我', '忘记密码？', '重置码', '新密码（重置用）', '重置密码',
    '密码已重置，请用新密码登录', '请联系管理员获取重置码',
    '请运行安装目录的重置工具获取重置码', '用户名或重置码无效。',
  ];
  const rows = html.slice(html.indexOf('const rows = ['));
  for (const key of keys) {
    const line = rows.split(/\r?\n/).find(line => line.trim().startsWith(`["${key}",`));
    assert.ok(line, `Missing locale row: ${key}`);
    const values = JSON.parse(line.trim().replace(/,$/, ''));
    assert.equal(values.length, 10, key);
    assert.ok(values.every(value => typeof value === 'string' && value.trim()), key);
  }
});

test('remembered tokens take precedence and switching storage clears stale tokens', () => {
  const storage = () => {
    const values = new Map();
    return {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: key => values.delete(key),
    };
  };
  const localStorage = storage();
  const sessionStorage = storage();
  const code = ['clearStoredToken', 'restoreToken', 'saveToken'].map(name => {
    const match = html.match(new RegExp(`function ${name}\\([^\\n]*?\\) \\{[\\s\\S]*?\\n\\}`));
    assert.ok(match, `Missing token storage function: ${name}`);
    return match[0];
  }).join('\n');
  const { restoreToken, saveToken, clearStoredToken } = runInNewContext(
    "const TOKEN_KEY = 'rf_token'; let AUTH_TOKEN;\n" + code +
      '\n({ restoreToken, saveToken, clearStoredToken })',
    { localStorage, sessionStorage },
  );

  sessionStorage.setItem('rf_token', 'old-session');
  localStorage.setItem('rf_token', 'remembered');
  assert.equal(restoreToken(), 'remembered');

  saveToken('session-only');
  assert.equal(localStorage.getItem('rf_token'), null);
  assert.equal(restoreToken(), 'session-only');

  saveToken('persisted', true);
  assert.equal(sessionStorage.getItem('rf_token'), null);
  assert.equal(restoreToken(), 'persisted');

  sessionStorage.setItem('rf_token', 'other-session');
  clearStoredToken();
  assert.equal(restoreToken(), null);
});
