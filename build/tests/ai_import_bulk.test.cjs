const assert = require('node:assert/strict');
const test = require('node:test');
const { confidenceGrade, bulkChunks } = require(process.env.RF_IMPORT_AI_SOURCE ||
  '../../custom_frontend/import_review_ai.js');

const grades = [
  ['same at threshold', {verdict:'same',confidence:0.9}, 'green'],
  ['same above threshold', {verdict:'same',confidence:1}, 'green'],
  ['same below threshold', {verdict:'same',confidence:0.8999}, 'amber'],
  ['same at lower threshold', {verdict:'same',confidence:0.6}, 'amber'],
  ['same with low confidence', {verdict:'same',confidence:0.59}, null],
  ['same with missing confidence', {verdict:'same',confidence:null}, null],
  ['different with high confidence', {verdict:'different',confidence:0.99}, null],
  ['unsure without confidence', {verdict:'unsure'}, 'amber'],
  ['degraded overrides same', {verdict:'same',confidence:1,degraded:true}, 'gray'],
  ['degraded overrides unsure', {verdict:'unsure',degraded:true}, 'gray']
];
for (const [label,row,grade] of grades) {
  test(label, () => assert.equal(confidenceGrade(row),grade));
}

test('bulk chunks keep input order, cover each item once, and cap at 100', () => {
  const actions = Array.from({length:256}, (_,index) => ({id:String(index)}));
  const chunks = [...bulkChunks(actions)];
  assert.deepEqual(chunks.map(chunk => chunk.length),[100,100,56]);
  assert.deepEqual(chunks.flat(),actions);
  assert.deepEqual([...bulkChunks([])],[]);
});
