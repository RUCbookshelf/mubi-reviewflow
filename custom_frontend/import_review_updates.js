/* Server-confirmed cache overlays prevent old background pages from undoing saves. */
(function(root){'use strict';
 const key=r=>r.record_id||r.review_id;
 const merge=(rows,updates)=>{const map=new Map(rows.map(r=>[key(r),r]));for(const r of updates||[])map.set(key(r),r);return [...map.values()];};
 /* r10-U2（导入12项 #11-A）：批量确认资格判据的前端镜像。后端
    custom_backend/import_review_actions.py 的 normalize_doi/js 同款与
    identity_key() 是唯一权威；本文件逐条复刻同一算法，只做按钮可用性预判
   （提交仍由后端复核，任何不一致都会整批回滚）。两端一致性由
   build/tests/test_r10_u2_bulk_identity.py 的跨语言对照矩阵钉住。
   两段判据任一成立即 eligible（仍需人工点一下确认，绝不自动判重）：
   A. DOI 一致：所有成员 DOI 规范化后非空且唯一；
   B. 无 DOI 的强身份一致：所有成员 DOI 均为空，且规范化标题、第一作者
      姓氏、年份三项两两相同且都非空。 */
 const PARTICLE=/^(van|von|der|den|de|di|da|del|della|la|le|du)$/i,SUFFIX=/^(jr|sr|ii|iii|iv)\.?$/i;
 const TAIL=new Set([...'.,;:!?>。，、；：！？”’…‥·】」』》']),PAIRS={')':'(',']':'[','}':'{','】':'【','」':'「','』':'『','”':'“','’':'‘'};
 function countChar(text,ch){let n=0;for(const c of text)if(c===ch)n++;return n;}
 /* 与 coscreen.normalize.normalize_doi 同步骤：删 Cf 不可见字符 → NFKC →
    去全部空白并小写 → 循环剥 doi.org/doi: 前缀 → 剥尾部引用标点。 */
 function normDoi(value){let s='';for(const ch of String(value||''))if(!/\p{Cf}/u.test(ch))s+=ch;
  s=s.normalize('NFKC').replace(/\s+/g,'').toLowerCase();
  for(;;){const m=/^(?:(?:https?:\/\/)?(?:www\.)?(?:dx\.)?doi\.org\/|doi\s*:)/i.exec(s);if(!m)break;s=s.slice(m[0].length);}
  while(s.length){const ch=[...s].pop();if(TAIL.has(ch)){s=[...s].slice(0,-1).join('');continue;}if(PAIRS[ch]&&countChar(s,PAIRS[ch])<countChar(s,ch)){s=[...s].slice(0,-1).join('');continue;}break;}
  return s;}
 /* 与后端 identity_key() 逐条同构：（规范化标题, 第一作者姓氏, 年份）。 */
 function identityKey(member){const article=member.article||(member.record&&member.record.article)||{};
  let title=[...String(article.title||'').normalize('NFKC').toLowerCase()].map(c=>/[\p{P}\p{S}]/u.test(c)?' ':c).join('').trim().replace(/\s+/g,' ').replace(/^ +| +$/g,'');
  const head=String(article.authors||'').split(';')[0].trim();let surname='';
  if(head.includes(','))surname=head.split(',')[0].trim();else{const tokens=head.split(/\s+/).filter(Boolean);while(tokens.length>1&&SUFFIX.test(tokens[tokens.length-1]))tokens.pop();/* 「姓 缩写」序（Smith J）：末位是缩写时姓氏取前一位。 */if(tokens.length>1&&/^[A-Z]{1,3}\.?$/.test(tokens[tokens.length-1]))tokens.pop();surname=tokens.length?tokens[tokens.length-1]:'';}
  const words=surname.split(/\s+/).filter(Boolean);while(words.length>1&&PARTICLE.test(words[0]))words.shift();
  return [title,words.join(' ').toLowerCase(),String(article.year==null?'':article.year).trim()];}
 function eligible(group){if(group.legacy||group.kind==='identity_conflict'||!['pending','undecided'].includes(group.status))return false;const members=group.members||[];if(members.length<2||members.filter(m=>m.ref_type==='active').length>1)return false;
  const dois=members.map(m=>normDoi((m.article||(m.record&&m.record.article)||{}).doi));
  if(dois.every(Boolean))return new Set(dois).size===1;
  if(dois.some(Boolean))return false;
  const keys=members.map(identityKey),titles=new Set(keys.map(k=>k[0])),surnames=new Set(keys.map(k=>k[1])),years=new Set(keys.map(k=>k[2]));
  return titles.size===1&&surnames.size===1&&years.size===1&&![...titles,...surnames,...years].includes('');}
 function create(){const records=new Map(),candidates=new Map(),legacy=new Map();return {apply(delta){for(const [name,map] of [['records',records],['candidates',candidates],['legacy',legacy]])for(const r of delta?.[name]||[])map.set(key(r),r);},merge(name,rows){return merge(rows,[...({records,candidates,legacy}[name]).values()]);}};}
 const needsReload=(baseline,version)=>Number(version)>Number(baseline);
 const canActivate=(batch,busy)=>!busy&&batch?.can_manage===true&&Number(batch.review_counts?.ready)>0;
 const api={merge,eligible,create,needsReload,canActivate,normDoi,identityKey};root.ImportReviewUpdates=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
