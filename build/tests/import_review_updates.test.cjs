const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const U=require('../../custom_frontend/import_review_updates.js');
test('server-confirmed delta overrides stale background pages and preserves order',()=>{const c=U.create();c.apply({records:[{record_id:'a',state:'ready'}],candidates:[{review_id:'g',status:'duplicate'}]});assert.deepEqual(c.merge('records',[{record_id:'a',state:'old'},{record_id:'b',state:'staged'}]),[{record_id:'a',state:'ready'},{record_id:'b',state:'staged'}]);assert.equal(c.merge('candidates',[{review_id:'g',status:'pending'}])[0].status,'duplicate');});
test('undo replaces previous overlays, and task cache is independently reset',()=>{const c=U.create();c.apply({records:[{record_id:'a',state:'ready'}]});c.apply({records:[{record_id:'a',state:'staged'}]});assert.equal(c.merge('records',[])[0].state,'staged');assert.deepEqual(U.create().merge('records',[]),[]);});
// G5 C8 (2026-11-10 门禁收尾): the frontend eligibility mirror (eligible()/identityKey())
// was deleted with G5 F2 — backend bulk_eligibility is the single source of truth.
// This pin fails if the mirror ever returns to the module surface (which would
// re-create the dual-source drift the cross-language matrix used to guard).
// The DOI/identity matrix itself is pinned backend-side in
// build/tests/test_r10_u2_bulk_identity.py and test_2_1_5_g5_merge_rules.py.
test('bulk eligibility mirror stays retired (backend bulk_eligible is the single source)',()=>{assert.equal(U.eligible,undefined);assert.equal(U.identityKey,undefined);});
test('compact copy has ten languages and consistent placeholders',()=>{const rows=[];vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../custom_frontend/import_review_refined_locale.js'),'utf8'),{RFLang:{register:r=>rows.push(...r)}});for(const row of rows){assert.equal(row.length,10);for(const value of row){assert.ok(value.trim());assert.deepEqual(value.match(/\{\d+\}/g)||[],row[1].match(/\{\d+\}/g)||[]);}}});

test('read baseline acknowledges the returned version; loaded ready records do not wait for history',()=>{assert.equal(U.needsReload(9,9),false);assert.equal(U.needsReload(9,10),true);assert.equal(U.canActivate({can_manage:true,review_counts:{ready:7}},false),true);assert.equal(U.canActivate({can_manage:false,review_counts:{ready:7}},false),false);assert.equal(U.canActivate({can_manage:true,review_counts:{ready:0}},false),false);assert.equal(U.canActivate({can_manage:true,review_counts:{ready:7}},true),false);});
