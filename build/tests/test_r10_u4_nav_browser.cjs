'use strict';
/* r10-u4 导航泳道真浏览器回归（F06 / F10 / F11 / F17 / F18 / F19 / F20）。
 *
 * 在隔离实例上跑真实应用（build/tests/test_r10_u4_nav_browser.py 负责起停实例并
 * 传入 URL），全部断言只看用户可见结果：卡片是否在、文本写的是什么、菜单 title、
 * 抽屉开关状态、toast 是否出现、点击后落到哪一页。
 *
 * 前置：
 *   REVIEWFLOW_R10U4_URL   隔离实例地址（必填，由 pytest 门控生成；端口避开其他泳道）
 *   REVIEWFLOW_PLAYWRIGHT  playwright 模块路径（缺省 'playwright'）
 *
 * 用法：node build/tests/test_r10_u4_nav_browser.cjs   （exit 0 = 全部通过）
 */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.REVIEWFLOW_PLAYWRIGHT||'playwright');
const url=(process.env.REVIEWFLOW_R10U4_URL||'').replace(/\/$/,'');
if(!url)throw new Error('REVIEWFLOW_R10U4_URL 未设置：本脚本必须由 pytest 门控在隔离实例上运行');
const user='u4browser@r10.test',password='R10U4-browser-2026!',taskName='r10-u4-nav';

(async()=>{
 const browser=await chromium.launch({headless:true});
 const errors=[];
 let checks=0;
 const check=async(name,fn)=>{try{await fn();checks++;console.log('PASS',name);}catch(e){console.log('FAIL',name);throw e;}};
 const page=async(token)=>{
  const p=await browser.newPage({viewport:{width:1440,height:950}});
  p.on('pageerror',e=>errors.push(e.message));
  if(token)await p.addInitScript(t=>sessionStorage.setItem('rf_token',t),token);
  /* 记录页面出现过的每一条 toast 文本：toast 有驻留时长，等状态就绪后再回读
     会与之竞态（注册后 enterApp 要拉若干接口）。观察 #toasts 的子节点，文案
     出现即留档。 */
  await p.addInitScript(()=>{
   window.__rfToasts=[];
   document.addEventListener('DOMContentLoaded',()=>{
    const host=document.getElementById('toasts');
    if(!host)return;
    new MutationObserver(records=>{
     for(const record of records)for(const node of record.addedNodes)
      if(node.nodeType===1&&node.classList.contains('toast'))window.__rfToasts.push(node.textContent);
    }).observe(host,{childList:true});
   });
  });
  return p;
 };
 /* 应用状态 S 是 index.html 内联脚本的顶层 const：裸名可读，window.S 恒为 undefined。 */
 const ready=p=>p.waitForFunction(()=>S&&S.user&&window.RFNavigation&&window.RFWorkspaceShell,null,{timeout:30000});
 /* 分析页在 loadAnalysis() 执行期间整体 inert（index.html:8844-8845 置位、:8912 恢复）。
     inert 子树里的输入框既点不动也 fill 不进（Playwright 的 fill 会静默失败），所以
     「页面已就绪」之后还必须等本页交互恢复，否则断言会在加载窗口内取样而假红。 */
 const editable=p=>p.waitForFunction(()=>{
   const page=document.querySelector('section[data-p="analysis"]');
   return !!page&&!page.inert&&!!window.RFAnalysisWorkspace;
 },null,{timeout:30000});
 /* enterApp() 在收尾还会按 location.hash 再 applyRoute 一次（index.html:9449）；登录后
    任务上下文落定又会触发 reviewflow:analysis-task-changed → `go(S.page)`（index.html:5922-5924），
    两者都会把「测试刚驱动的路由」按片段重置回目录。所以进入分析页后先等应用自己把
    初始片段落地并静止（目录打开、无当前方法），再开始驱动路由。 */
 const settled=p=>p.waitForFunction(()=>{
   const catalog=document.getElementById('anMethodCatalog');
   return !!catalog&&!catalog.hidden&&window.RFAnalysisWorkspace.currentRoute().page==='analysis'
     && !window.RFAnalysisWorkspace.currentRoute().tool;
 },null,{timeout:30000});
 /* 分析工作区的路由是异步落地的：applyRoute 只是发起，真正的 renderTool 可能在下一轮。
     断言前先等目标方法在 currentRoute() 里生效，再等它的落地卡可见。 */
 const routeTo=async(p,route,cardId)=>{
  await p.evaluate(r=>window.RFAnalysisWorkspace.applyRoute(r,{history:false}),route);
  if(route.tool)await p.waitForFunction(t=>window.RFAnalysisWorkspace.currentRoute().tool===t,route.tool,{timeout:15000});
  if(cardId)await p.waitForFunction(id=>{
    const card=document.getElementById(id);
    return !!card&&!card.classList.contains('an-view-hidden');
  },cardId,{timeout:15000});
 };
 /* 静默期：登录收尾的补课与 task-context-changed 的重放都是「再过一拍才发生」的，
     相隔约 200-300ms。这里等到连续 QUIET_MS 没有任何 analysis-view-changed 事件，
     确保后续断言不会被应用自己的重放改写。 */
 const QUIET_MS=700;
 const quiesce=p=>p.evaluate(async ms=>{
   await new Promise(resolve=>{
     let timer=setTimeout(resolve,ms);
     const tick=()=>{clearTimeout(timer);timer=setTimeout(resolve,ms);};
     document.addEventListener('reviewflow:analysis-view-changed',tick);
     document.addEventListener('reviewflow:analysis-task-changed',tick);
     document.addEventListener('reviewflow:workspace-changed',tick);
     setTimeout(()=>{document.removeEventListener('reviewflow:analysis-view-changed',tick);
       document.removeEventListener('reviewflow:analysis-task-changed',tick);
       document.removeEventListener('reviewflow:workspace-changed',tick);},ms*4);
   });
 },QUIET_MS);
 const close=async p=>{await p.unrouteAll({behavior:'wait'}).catch(()=>{});await p.close();};

 // 账号与任务：注册（已存在则登录）后建一个任务，供分析页使用。
 const signUp=async()=>{
  const response=await fetch(url+'/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:user,password})});
  return response.ok?response.json():null;
 };
 let token=(await signUp())?.token;
 if(!token){
  const response=await fetch(url+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:user,password})});
  assert.ok(response.ok,'前置登录失败：'+response.status);
  token=(await response.json()).token;
 }
 const authHeaders={Authorization:'Bearer '+token};
 const existing=await (await fetch(url+'/api/tasks?include_archived=true',{headers:authHeaders})).json();
 if(!(existing.tasks||[]).some(task=>task.name===taskName)){
  const taskResponse=await fetch(url+'/api/tasks',{method:'POST',
   headers:{...authHeaders,'Content-Type':'application/json'},
   body:JSON.stringify({name:taskName})});
  assert.ok(taskResponse.ok,'前置任务创建失败：'+taskResponse.status);
  await taskResponse.json();
 }

 // ---------------------------------------------------------------- F11
 await check('F11 注册成功后告知“独立用户/下一步”，登录路径不误报',async()=>{
  const suffix=Date.now().toString().slice(-8);
  const p=await page(null);
  await p.goto(url+'/#',{waitUntil:'domcontentloaded'});
  await p.locator('#authSwitch').click();
  await p.locator('#authUser').fill('u4reg'+suffix);
  await p.locator('#authPass').fill('R10U4-register-2026!');
  await p.locator('#authSubmit').click();
  await p.waitForFunction(()=>S&&S.user,null,{timeout:30000});
  await p.waitForFunction(()=>window.__rfToasts.length>0,null,{timeout:15000});
  const texts=await p.evaluate(()=>window.__rfToasts.slice());
  assert.ok(texts.some(text=>text.includes('你已注册为独立用户')),'注册成功缺少角色说明：'+JSON.stringify(texts));
  assert.ok(texts.some(text=>text.includes('欢迎，')),'原有欢迎语不得丢失（对照组）');
  await close(p);
  const p2=await page(null);
  await p2.goto(url+'/#',{waitUntil:'domcontentloaded'});
  await p2.locator('#authUser').fill(user);
  await p2.locator('#authPass').fill(password);
  await p2.locator('#authSubmit').click();
  await p2.waitForFunction(()=>S&&S.user,null,{timeout:30000});
  await p2.waitForFunction(()=>window.__rfToasts.length>0,null,{timeout:15000});
  const loginTexts=await p2.evaluate(()=>window.__rfToasts.slice());
  assert.equal(loginTexts.some(text=>text.includes('你已注册为独立用户')),false,'登录不应重复“已注册”提示');
  assert.ok(loginTexts.some(text=>text.includes('欢迎，')),'登录的欢迎语不得改变（对照组）');
  await close(p2);
 });

 // ---------------------------------------------------------------- F18
 await check('F18 坏深链给出一次性提示并落首页；正常深链不误报',async()=>{
  const p=await page(token);
  await p.goto(url+'/#workspace/not-a-real-page',{waitUntil:'domcontentloaded'});
  await ready(p);
  await p.waitForFunction(()=>[...document.querySelectorAll('.toast')].some(n=>n.textContent.includes('未识别的链接')),null,{timeout:9000});
  assert.equal(await p.evaluate(()=>S.page),'home','坏深链必须安全回首页');
  const count=await p.evaluate(()=>[...document.querySelectorAll('.toast')].filter(n=>n.textContent.includes('未识别的链接')).length);
  assert.equal(count,1,'提示只应出现一次');
  await close(p);
  const p2=await page(token);
  await p2.goto(url+'/#workspace/analysis',{waitUntil:'domcontentloaded'});
  await ready(p2);
  await p2.waitForTimeout(800);
  assert.equal(await p2.evaluate(()=>[...document.querySelectorAll('.toast')].some(n=>n.textContent.includes('未识别的链接'))),false,'正常深链不得误报');
  assert.equal(await p2.evaluate(()=>S.page),'analysis');
  await close(p2);
 });

 // ---------------------------------------------------------------- F06
 await check('F06 目录页顶部有新手路径卡：三步走同一注册表，分组/搜索时收起',async()=>{
  const p=await page(token);
  await p.goto(url+'/#workspace/analysis',{waitUntil:'domcontentloaded'});
  await ready(p);
  await editable(p);
  await settled(p);
  await quiesce(p);
  const path=p.locator('#anBeginnerPath');
  await path.waitFor({state:'visible',timeout:10000});
  const info=await p.evaluate(()=>{
   const path=document.getElementById('anBeginnerPath');
   const catalog=document.getElementById('anMethodCatalog');
   return {
    groups:window.RFNavigation.groups.map(group=>group.label),
    steps:[...path.querySelectorAll('a')].map(a=>({text:a.textContent.trim(),hash:a.hash})),
    first:path===catalog.querySelector('.an-catalog-group'),
    title:path.querySelector('h4').textContent,
    intro:[...path.querySelectorAll('header p')].map(node=>node.textContent).join(' ')
   };
  });
  assert.equal(info.title,'新手路径');
  assert.match(info.intro,/数据/, '说明文字必须讲清三步顺序');
  assert.equal(info.steps.length,3,'新手路径必须恰好三步');
  const targets=info.steps.map(step=>decodeURIComponent(step.hash));
  assert.deepEqual(targets,['#workspace/analysis/data','#workspace/analysis/effects','#workspace/analysis/synthesis'],
    '三步顺序必须与注册表模块顺序一致（数据准备→效应量计算→总体效应合并）');
  assert.deepEqual(info.steps.map(step=>step.text.replace(/^\d+\s*·\s*/,'')),[info.groups[0],info.groups[1],info.groups[2]],
    '步骤文字必须取自注册表模块名');
  assert.equal(info.first,true,'新手路径卡必须是目录顶部第一块');
  // 分组筛选：卡片让位，分组目录独占。
  await p.evaluate(()=>window.RFAnalysisWorkspace.applyRoute({page:'analysis',group:'sensitivity'},{history:false}));
  assert.equal(await path.isVisible(),false,'进入分组目录后新手路径卡应收起');
  // 搜索：卡片让位，只留命中结果。
  await p.evaluate(()=>window.RFAnalysisWorkspace.applyRoute({page:'analysis'},{history:false}));
  await path.waitFor({state:'visible',timeout:5000});
  await editable(p);
  await p.locator('#anCatalogSearch').fill('Egger');
  assert.equal(await path.isVisible(),false,'搜索状态应收起新手路径卡');
  await p.locator('#anCatalogSearch').fill('');
  await path.waitFor({state:'visible',timeout:5000});
  await editable(p);
  // 点第一步真的落到该模块目录。
  await p.locator('#anBeginnerPath a').first().click();
  await p.waitForFunction(()=>location.hash==='#workspace/analysis/data',null,{timeout:9000});
  await close(p);
 });

 // ---------------------------------------------------------------- F17
 await check('F17 fragment/录入方式工具落地时有上下文条（方法·模块·返回目录），普通工具没有',async()=>{
  const p=await page(token);
  await p.goto(url+'/#workspace/analysis',{waitUntil:'domcontentloaded'});
  await ready(p);
  await editable(p);
  await settled(p);
  await quiesce(p);
  const read=()=>p.evaluate(()=>{
   const node=document.getElementById('anLandingContext');
   if(!node)return null;
   return {hidden:node.hidden,text:node.textContent,
     card:node.parentElement&&node.parentElement.id,
     link:node.querySelector('a')&&node.querySelector('a').hash};
  });
  await routeTo(p,{page:'analysis',group:'bias',tool:'peters'},'anSingleGroupPanel');
  let ctx=await read();
  assert.ok(ctx&&!ctx.hidden,'Peters（fragment 工具）必须显示上下文条');
  assert.equal(ctx.card,'anSingleGroupPanel','上下文条必须常驻在该方法自己的落地卡里');
  assert.match(ctx.text,/当前方法：Peters 单组比例检验/,'必须写出当前方法名');
  assert.match(ctx.text,/分析模块：发表偏倚检验/,'必须写出所属模块');
  assert.equal(decodeURIComponent(ctx.link),'#workspace/analysis','必须提供返回目录入口');
  // 换到另一张 fragment 卡（同一 shared view 的另一个 fragment），上下文条跟着走。
  await routeTo(p,{page:'analysis',group:'bias',tool:'trim-fill'},'anJumpBias');
  ctx=await read();
  assert.ok(ctx&&!ctx.hidden,'剪补法同样需要上下文条');
  assert.match(ctx.text,/当前方法：剪补法敏感性分析/);
  // 录入方式工具（entryMode）同样有：它们的落地卡是 anJumpEffects，卡 id 不等于 view，
  // 上下文条必须按同一套「落地卡」判定去挂，否则零单元格这类入口永远看不到定位条。
  await routeTo(p,{page:'analysis',group:'sensitivity',tool:'zero-cells'},'anJumpEffects');
  ctx=await read();
  assert.ok(ctx&&!ctx.hidden,'recordMode 工具（零单元格）需要上下文条');
  assert.equal(ctx.card,'anJumpEffects','entryMode 的上下文条必须挂在它实际的落地卡里');
  assert.match(ctx.text,/当前方法：零单元格敏感性分析/);
  // 普通工具：不得凭空多出一条常驻条。
  await routeTo(p,{page:'analysis',group:'synthesis',tool:'pooled-effect'},'anJumpEffects');
  ctx=await read();
  assert.ok(!ctx||ctx.hidden,'非 fragment 工具不应显示上下文条（对照组）');
  // 返回目录链接真的回目录。
  await routeTo(p,{page:'analysis',group:'bias',tool:'peters'},'anSingleGroupPanel');
  await p.locator('#anLandingContext a').click();
  await p.waitForFunction(()=>document.getElementById('anMethodCatalog')&&!document.getElementById('anMethodCatalog').hidden,null,{timeout:9000});
  ctx=await read();
  assert.ok(!ctx||ctx.hidden,'回到目录后上下文条必须撤掉');
  await close(p);
 });

 // ---------------------------------------------------------------- F20
 await check('F20 阶段菜单项 title 写出真实目的地（含 stageLast 记忆）',async()=>{
  const p=await page(token);
  await p.goto(url+'/#workspace/screen',{waitUntil:'domcontentloaded'});
  await ready(p);
  await p.waitForFunction(()=>S.page==='screen',null,{timeout:30000});
  /* stageLast 只在共享导航 navigate() 里记录（workspace_shell.js:180-181），直接调
     window.go() 绕过了它——必须走共享导航才能验证「记住上次页面」这条语义，否则测的
     是测试自己的桩行为。这里调 RFWorkspaceShell.navigate（应用自己的入口，侧栏项、
     面包屑、深链最终都汇到它），不手搓 stageLast。 */
  await p.evaluate(()=>window.RFWorkspaceShell.navigate({page:'fulltext'}));
  await p.waitForFunction(()=>S.page==='fulltext',null,{timeout:15000});
  await p.evaluate(()=>window.RFWorkspaceShell.refresh());
  const titles=await p.evaluate(()=>{
   const trigger=[...document.querySelectorAll('.rf-crumb-level .rf-stage-trigger')][0];
   trigger.click();
   return [...document.querySelectorAll('#rfLevelMenu-stage a')].map(a=>({text:a.textContent,title:a.title,hash:a.hash}));
  });
  assert.equal(titles.length,3,'阶段菜单应有三项');
  assert.equal(titles[0].title,'将前往：文献复筛','「筛选」项必须显示上次停留的复筛页，而不是阶段名');
  assert.equal(titles[1].title,'将前往：编码方案','未被访问过的阶段回落到注册表默认页');
  assert.equal(titles[2].title,'将前往：数据分析');
  assert.deepEqual(titles.map(item=>item.title.replace('将前往：','')),
    ['文献复筛','编码方案','数据分析'],'title 目的地必须来自注册表页名');
  await close(p);
 });

 // ---------------------------------------------------------------- F10
 await check('F10 展开抽屉后经导航切换页面，抽屉自动收起',async()=>{
  const p=await page(token);
  await p.goto(url+'/#workspace/home',{waitUntil:'domcontentloaded'});
  await ready(p);
  await p.waitForFunction(()=>{
   const sheet=window.RFNavigationSheet,toggle=document.getElementById('rfSidebarToggle');
   return !!sheet&&!!toggle&&toggle.getAttribute('aria-expanded')!==null;
  },null,{timeout:15000});
  /* 登录收尾的 go()/任务上下文落定还会各派发一次 workspace-changed，每次都会按
     「路由变了就收起」的规则关掉抽屉。等这些收尾事件停稳，再验证「导航才收起、
     不导航不收起」，否则对照组测到的是收尾事件而不是本条行为。 */
  await quiesce(p);
  await p.evaluate(()=>window.RFNavigationSheet.setOpen(true,{immediate:true}));
  await p.waitForTimeout(250);
  assert.equal(await p.evaluate(()=>document.getElementById('rfSidebarToggle').getAttribute('aria-expanded')),'true','前置：抽屉应已展开');
  const expanded=()=>p.evaluate(()=>document.getElementById('rfSidebarToggle').getAttribute('aria-expanded'));
  // 抽屉里点侧栏导航（真实用户路径）。
  await p.locator('#appShell [data-nav="export"]').click();
  await p.waitForFunction(()=>S.page==='export',null,{timeout:9000});
  await p.waitForFunction(()=>document.getElementById('rfSidebarToggle').getAttribute('aria-expanded')!=='true',null,{timeout:9000});
  assert.equal(await expanded(),'false','经导航后抽屉必须收起（否则盖住新页面）');
  // 收起后页面可点：目标页主区未 inert。
  assert.equal(await p.evaluate(()=>document.getElementById('appMain').inert),false,'收起后主区必须可操作');
  // 对照：单纯同页无导航不收起（同样要等到导航副作用停稳，再展开观察）。
  await quiesce(p);
  await p.evaluate(()=>window.RFNavigationSheet.setOpen(true,{immediate:true}));
  await p.waitForTimeout(400);
  assert.equal(await expanded(),'true','未导航时抽屉保持展开（对照组）');
  await close(p);
 });

 // ---------------------------------------------------------------- F19
 await check('F19 初筛/编码/分析的帮助入口可见、文案随语言切换且能跳到帮助页',async()=>{
  const p=await page(token);
  await p.goto(url+'/#workspace/home',{waitUntil:'domcontentloaded'});
  await ready(p);
  const targets=[
   {page:'screen',menu:'details.rfl-reading',zh:'使用文档',en:'View documentation'},
   {page:'bench',menu:'details[data-preview-evidence-menu="coding"]',zh:'查看帮助',en:'View help'},
   {page:'analysis',menu:null,zh:'查看帮助',en:'View help'},
  ];
  for(const language of ['zh','en']){
   if(language==='en'){
    await p.evaluate(()=>window.RFLang.set('en'));
    await p.waitForFunction(()=>document.documentElement.lang==='en',null,{timeout:9000});
   }
   for(const target of targets){
    await p.evaluate(name=>window.go(name,{guarded:true}),target.page);
    await p.waitForFunction(name=>S.page===name,target.page,{timeout:15000});
    const section=p.locator('.page[data-p="'+target.page+'"]');
    const scope=target.menu?section.locator(target.menu):section;
    if(target.menu && !await scope.evaluate(n=>n.open)){
     await scope.locator(':scope > summary').click();
    }
    const entry=scope.locator('[data-rf-help-entry="'+target.page+'"]');
    await entry.waitFor({state:'visible',timeout:9000});
    await p.waitForFunction(({name,label})=>{
     const entry=document.querySelector('.page[data-p="'+name+'"] [data-rf-help-entry="'+name+'"]');
     return entry?.textContent.trim()===label;
    },{name:target.page,label:target[language]},{timeout:9000});
    assert.equal((await entry.innerText()).trim(),target[language],target.page+' 页帮助入口文案不对');
    assert.equal(await entry.getAttribute('href'),'#workspace/help');
    await entry.click();
    await p.waitForFunction(()=>S.page==='help',null,{timeout:9000});
    assert.equal(await p.locator('.page[data-p="help"]').isVisible(),true);
   }
  }
  await close(p);
 });

 assert.deepEqual(errors,[],'页面脚本错误：\n'+errors.join('\n'));
 console.log(JSON.stringify({checks,errors:errors.length}));
 await browser.close();
})().catch(async error=>{
 console.error(String(error&&error.stack||error).replace(/Bearer [^\s'"\u001b]+/g,'Bearer [redacted]'));
 process.exit(1);
});
