/* Server-confirmed cache overlays prevent old background pages from undoing saves. */
(function(root){'use strict';
 const key=r=>r.record_id||r.review_id;
 const merge=(rows,updates)=>{const map=new Map(rows.map(r=>[key(r),r]));for(const r of updates||[])map.set(key(r),r);return [...map.values()];};
 function create(){const records=new Map(),candidates=new Map(),legacy=new Map();return {apply(delta){for(const [name,map] of [['records',records],['candidates',candidates],['legacy',legacy]])for(const r of delta?.[name]||[])map.set(key(r),r);},merge(name,rows){return merge(rows,[...({records,candidates,legacy}[name]).values()]);}};}
 const needsReload=(baseline,version)=>Number(version)>Number(baseline);
 const canActivate=(batch,busy)=>!busy&&batch?.can_manage===true&&Number(batch.review_counts?.ready)>0;
 const api={merge,create,needsReload,canActivate};root.ImportReviewUpdates=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
