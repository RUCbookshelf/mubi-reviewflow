'use strict';
const { readFileSync } = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const frontend = path.join(__dirname, '../../custom_frontend');
const localeFiles = ['ai_providers_locale.js', 'import_review_aiux_locale.js'];
const providerIds = ['openai', 'anthropic', 'google', 'deepseek', 'qwen', 'zhipu', 'moonshot',
  'minimax', 'xai', 'mistral', 'opencode_go', 'commandcode_goat', 'local_ollama'];

function placeholders(text) {
  return [...text.matchAll(/\{[a-zA-Z0-9_]+\}/g)].map(match => match[0]).sort();
}

test('AIUX registers ten nonempty locales with consistent interpolation', () => {
  const rows = [];
  const RFLang = { register: entries => rows.push(...entries) };
  for (const file of localeFiles) {
    runInNewContext(readFileSync(path.join(frontend, file), 'utf8'), { window: { RFLang }, RFLang }, { filename: file });
  }
  assert.ok(rows.length > providerIds.length);
  const aliases = new Set();
  for (const row of rows) {
    assert.equal(row.length, 10, row[0]);
    for (const value of row) {
      assert.ok(typeof value === 'string' && value.trim(), row[0]);
      assert.deepEqual(placeholders(value), placeholders(row[0]), value);
    }
    for (const alias of row.aliases || []) {
      assert.ok(!aliases.has(alias), 'duplicate alias: ' + alias);
      aliases.add(alias);
    }
  }
  for (const id of providerIds) assert.ok(aliases.has('ai_providers.' + id), id);
  for (const name of ['cross_check', 'apply_high', 'export_report', 'agreement'])
    assert.ok(aliases.has('ai_import.' + name), name);
});

test('the catalog and localization load before either AI key surface', () => {
  const html = readFileSync(path.join(frontend, 'index.html'), 'utf8');
  const loadIndex = file => {
    const index = html.search(new RegExp('(?:src|data-rf-app-src)="\\./' + file.replaceAll('.', '\\.') + '\\?v=20261112-(?:aiux|ui-polish)"'));
    assert.ok(index >= 0, 'missing AIUX script: ' + file);
    return index;
  };
  const consumers = ['import_review_ai.js', 'ai_suggest.js'].map(loadIndex);
  for (const file of [...localeFiles, 'ai_providers_catalog.js']) {
    const index = loadIndex(file);
    assert.ok(consumers.every(consumer => index < consumer), file);
  }
});
