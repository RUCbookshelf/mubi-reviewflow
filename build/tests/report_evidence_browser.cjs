'use strict';
// report_evidence 前端行为浏览器回归（G21）：在隔离实例上实测 F06/F07/F08/F09/F20，
// 全程断言用户可见结果（按钮禁用态 / 对话框内错误与恢复提示 / 字段值 / toast）。
//
// 前置（实例与账号准备，参考 E:/rf214-playwright 的泳道约定）：
//   1) 隔离实例：COBOOKSHELF_DATA_DIR 指向新的独立目录（如 E:/uxt-r4-frontend）、
//      端口避开其他泳道（本文件默认 18772），代码取自本工作树 build.launcher：
//      `python run_instance.py`（后台常驻），health 200 后执行 prep.py 注册账号、
//      建任务并向 owner 库种入 3 篇文献，写出 session.json（{token,task,...}）；
//      prep.py 同时复位证据工作区（revision/撤销栈/幂等日志），每次跑 cjs 前重跑即可重复执行。
//   2) 运行：
//      REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
//      REVIEWFLOW_GUARD_SESSION=E:/uxt-r4-frontend/session.json \
//      node build/tests/report_evidence_browser.cjs   （exit 0 = 全部通过）
//   3) 跑完停实例并确认 health 已 000。
// 覆盖：
//   F06 撤销按钮栈空禁用且不因点击其他按钮/自身收尾/重开而复活；
//   F07 change 在途（PUT 人为延迟）时个人撤销与共享裁决被 saving 互斥拒绝，
//       延迟结束后状态一致（解除→撤销可恢复原关系）；
//   F08 关联对话框编辑中途关闭→sessionStorage 暂存→重开恢复（含恢复提示）；
//   F09 另一窗口推进 revision 后保存 409→自动刷新→提示冲突文案→原对话框重试成功；
//   F20 置信度草稿按方法分槽：JBI 草稿暂存后，方法切到主题综合不串槽（空表单、无恢复提示）。
// 说明：本文件只做行为断言与证据留档，不修改任何产品代码。
const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require(process.env.REVIEWFLOW_PLAYWRIGHT||'playwright');
const url=process.env.REVIEWFLOW_EVIDENCE_URL||'http://127.0.0.1:18772';
const sessionPath=process.env.REVIEWFLOW_GUARD_SESSION||'E:/uxt-r4-frontend/session.json';
const session=JSON.parse(fs.readFileSync(sessionPath,'utf8'));
const artifacts=process.env.REVIEWFLOW_EVIDENCE_ARTIFACTS||'E:/uxt-r4-frontend/artifacts';
fs.mkdirSync(artifacts,{recursive:true});

(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 // 只对保存 PUT 做可控行延迟（F07 在途窗口），其余请求原样放行。
 const net={putDelayMs:0};
 await page.route('**/report-evidence**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(req.method()==='PUT'&&path.endsWith('/report-evidence')&&net.putDelayMs>0)
   await new Promise(r=>setTimeout(r,net.putDelayMs));
  await route.fulfill({response:await route.fetch()});
 });
 await page.addInitScript(token=>sessionStorage.setItem('rf_token',token),session.token);
 await page.goto(url+'/#workspace/home');
 await page.waitForFunction(id=>S.user&&S.task&&S.task.task_id===id,session.task,{timeout:20000});
 await page.waitForSelector('#anJumpStudies #mp-study-manage',{state:'attached',timeout:20000}); // report_evidence 已装载并挂载入口（分析页未显示时为隐藏态）

 const dlg=()=>page.locator('dialog.mp-dialog[open]');
 const field=(label,kind='textarea')=>dlg().locator(`.mp-field:has(span:text-is("${label}")) ${kind}`);
 const toastHas=text=>page.waitForFunction(s=>[...document.querySelectorAll('.toast')].some(x=>x.textContent.includes(s)),text,{timeout:9000});
 // 分析页按工具路由哈希显示子视图（研究与报告 / 定性证据综合），与 openRelated 的跳转方式一致。
 const gotoAnalysis=async(toolHash,cardSel)=>{
  await page.evaluate(()=>go('analysis'));
  await page.waitForFunction(()=>{const p=document.querySelector('.page[data-p="analysis"]');return p&&!p.inert&&p.getAttribute('aria-busy')!=='true';},null,{timeout:15000});
  await page.evaluate(h=>{location.hash=h;},toolHash);
  await page.waitForSelector(cardSel,{timeout:15000});};
 const STUDIES=['#workspace/analysis/data/studies','#anJumpStudies:not(.an-view-hidden)'];
 const QUAL=['#workspace/analysis/special/qualitative','#anJumpQual:not(.an-view-hidden)'];
 let checks=0;
 const check=async(name,fn)=>{try{await fn();checks++;console.log('PASS',name);}catch(e){await page.screenshot({path:artifacts+'/fail-'+name.replace(/[^\w一-龥]+/g,'_').slice(0,40)+'.png',fullPage:true});throw e;}};

 await check('F06 栈空时撤销按钮禁用，点击其他按钮与重开都不复活',async()=>{
  await gotoAnalysis(...STUDIES);
  const open=async()=>{await page.locator('#mp-study-manage').click();await dlg().waitFor();};
  await open();
  assert.equal(await dlg().locator('h2').textContent(),'研究与样本关系');
  const undo=dlg().getByRole('button',{name:'撤销最近变更'});
  assert.equal(await undo.isDisabled(),true,'撤销在栈空时必须禁用');
  assert.equal(await dlg().getByText('没有关联；普通文献可照常筛选和编码。').count(),1);
  await dlg().locator('footer').getByRole('button',{name:'关闭',exact:true}).click(); // 点击其他按钮后关闭
  await dlg().waitFor({state:'detached'});
  await open(); // 重开：不得复活为可用
  assert.equal(await dlg().getByRole('button',{name:'撤销最近变更'}).isDisabled(),true,'重开后撤销仍必须禁用');
  await dlg().locator('footer').getByRole('button',{name:'关闭',exact:true}).click();
  await dlg().waitFor({state:'detached'});
 });

 await check('F08 关联对话框编辑中途关闭暂存，重开恢复未保存草稿',async()=>{
  await page.locator('#mp-study-link').click();await dlg().waitFor();
  assert.equal(await dlg().locator('h2').textContent(),'关联报告');
  const evidence=field('依据／原文片段（可稍后补充）');
  await evidence.fill('未保存的依据草稿 F08');
  await dlg().getByRole('button',{name:'取消',exact:true}).click();
  await dlg().waitFor({state:'detached'});
  await page.locator('#mp-study-link').click();await dlg().waitFor();
  assert.equal(await dlg().getByText('已恢复本浏览器暂存的未保存草稿。').count(),1,'重开必须出现恢复提示');
  assert.equal(await evidence.inputValue(),'未保存的依据草稿 F08','暂存内容必须回填字段');
  await evidence.fill(''); // 改回初始值即清槽，为后续场景让路
  await dlg().getByRole('button',{name:'取消',exact:true}).click();
  await dlg().waitFor({state:'detached'});
 });

 await check('F20 置信度草稿按方法分槽：JBI 草稿不串入主题综合',async()=>{
  const openConf=async()=>{await page.locator('#mp-qual-tools').click();await dlg().waitFor();
   await dlg().getByRole('button',{name:'证据置信度'}).click();await dlg().waitFor();};
  await gotoAnalysis(...QUAL);
  await openConf();
  assert.equal(await dlg().locator('h2').textContent(),'综合发现的 ConQual 评估','默认方法 jbi → ConQual');
  const finding=field('综合发现');
  await finding.fill('JBI 置信度草稿');
  await dlg().getByRole('button',{name:'取消',exact:true}).click();await dlg().waitFor({state:'detached'});
  await openConf(); // 同方法重开：恢复自己的草稿
  assert.equal(await dlg().getByText('已恢复本浏览器暂存的未保存草稿。').count(),1);
  assert.equal(await finding.inputValue(),'JBI 置信度草稿');
  await dlg().getByRole('button',{name:'取消',exact:true}).click();await dlg().waitFor({state:'detached'});
  // 切换方法：首页 → 数据提取与编码面板 → 综述路径与综合方法（可选）→ 保存为 qual+thematic
  await page.evaluate(()=>go('home'));
  await page.waitForSelector('.wf-tabs',{timeout:15000});
  await page.evaluate(()=>WorkflowControls.homeTab('coding'));
  await page.waitForSelector('#wf-panel-coding',{timeout:15000});
  await page.locator('#mp-method-settings > summary').click();
  await page.getByRole('button',{name:'设置综述路径'}).click();await dlg().waitFor();
  await dlg().locator('select[aria-label="综述路径"]').selectOption('qual');
  await dlg().locator('select[aria-label="定性综合方法"]').selectOption('thematic');
  await dlg().getByRole('button',{name:'保存设置'}).click();
  await toastHas('已保存个人综述路径与综合方法');
  await dlg().waitFor({state:'detached'});
  await gotoAnalysis(...QUAL);
  await openConf(); // 主题综合：不得出现 JBI 草稿（不串槽）
  assert.equal(await dlg().locator('h2').textContent(),'综合发现的 CERQual 评估','方法 thematic → CERQual');
  assert.equal(await dlg().getByText('已恢复本浏览器暂存的未保存草稿。').count(),0,'其他方法的草稿不得恢复到本方法');
  assert.equal(await finding.inputValue(),'','其他方法的草稿不得串入综合发现字段');
  await dlg().getByRole('button',{name:'取消',exact:true}).click();await dlg().waitFor({state:'detached'});
 });

 await check('F07 change 在途时个人撤销被互斥拒绝，落库后撤销可恢复',async()=>{
  await gotoAnalysis(...STUDIES);
  await page.locator('#mp-study-link').click();await dlg().waitFor();
  await dlg().locator('select[aria-label="选择相关报告"]').selectOption('R2');
  await dlg().getByRole('button',{name:'保存我的线索'}).click();
  await toastHas('个人线索已保存');await dlg().waitFor({state:'detached'});
  net.putDelayMs=900; // 打开在途窗口
  await page.locator('#mp-study-manage').click();await dlg().waitFor();
  const undo=dlg().getByRole('button',{name:'撤销最近变更'});
  assert.equal(await undo.isDisabled(),false,'保存后撤销应可用');
  await dlg().getByRole('button',{name:'解除',exact:true}).click(); // change 在途
  await undo.click(); // 在途并发撤销：必须被 saving 互斥拒绝
  await dlg().locator('.mp-error').waitFor();
  assert.match(await dlg().locator('.mp-error').textContent(),/正在保存，请稍候/);
  await dlg().getByText('没有关联；普通文献可照常筛选和编码。').waitFor(); // 解除落库
  net.putDelayMs=0;
  await undo.click(); // 互斥解除后撤销恢复关系
  await dlg().locator('article.mp-pair').filter({hasText:'First Report'}).waitFor();
  assert.equal(await undo.isDisabled(),true,'撤销消费完栈后不得复活（F06 收尾链）');
  await dlg().locator('footer').getByRole('button',{name:'关闭',exact:true}).click();await dlg().waitFor({state:'detached'});
 });

 await check('F07 change 在途时共享裁决（确认关系）同样被互斥拒绝',async()=>{
  await page.evaluate(()=>go('merge'));
  await page.waitForSelector('#mp-arbitration-link',{timeout:15000});
  await page.locator('#mp-arbitration-link').click();await dlg().waitFor();
  assert.equal(await dlg().locator('h2').textContent(),'报告关系裁决');
  net.putDelayMs=900;
  await dlg().getByRole('button',{name:'解除',exact:true}).click(); // change 在途
  await dlg().getByRole('button',{name:'确认关系'}).click(); // 并发裁决：必须被拒绝
  await dlg().locator('.mp-error').waitFor();
  assert.match(await dlg().locator('.mp-error').textContent(),/正在保存，请稍候/);
  await dlg().getByText('没有关联；普通文献可照常筛选和编码。').waitFor();
  net.putDelayMs=0;
  await dlg().locator('footer').getByRole('button',{name:'关闭',exact:true}).click();await dlg().waitFor({state:'detached'});
 });

 await check('F09 他窗推进 revision 后 409：自动刷新并以新 revision 重试成功',async()=>{
  // 另一「窗口」直接 PUT 推进 revision（双入口造冲突）
  const base=url+'/api/tasks/'+encodeURIComponent(session.task)+'/report-evidence';
  const headers={'Authorization':'Bearer '+session.token,'Content-Type':'application/json','X-Request-ID':crypto.randomUUID()};
  const current=await(await fetch(base,{headers})).json();
  const bumped=await fetch(base,{method:'PUT',headers:{...headers,'X-Request-ID':crypto.randomUUID()},
   body:JSON.stringify({expected_revision:current.revision,operation_id:crypto.randomUUID(),state:current.state,label:'另一窗口保存',stage:'coding'})});
  assert.equal(bumped.status,200,'外部窗口推进 revision 必须成功');
  await gotoAnalysis(...STUDIES);
  await page.locator('#mp-study-link').click();await dlg().waitFor();
  await field('依据／原文片段（可稍后补充）').fill('F09 冲突重试草稿');
  await dlg().getByRole('button',{name:'保存我的线索'}).click();
  await dlg().locator('.mp-error').waitFor();
  assert.match(await dlg().locator('.mp-error').textContent(),/已自动刷新为最新内容/,'必须呈现冲突自动刷新文案');
  assert.equal(await dlg().count(),1,'冲突后对话框保留现场供重试');
  await dlg().getByRole('button',{name:'保存我的线索'}).click(); // 自动刷新后的 revision 应使重试成功
  await toastHas('个人线索已保存');await dlg().waitFor({state:'detached'});
  await page.locator('#mp-study-manage').click();await dlg().waitFor();
  assert.equal(await dlg().getByText('F09 冲突重试草稿').count(),1,'重试保存的内容必须落库并可见');
  await dlg().locator('footer').getByRole('button',{name:'关闭',exact:true}).click();await dlg().waitFor({state:'detached'});
 });

 assert.deepEqual(errors,[],'页面不得抛出未捕获脚本错误');
 console.log(JSON.stringify({checks,errors,url,task:session.task}));
 await browser.close();
})().catch(error=>{console.error(String(error.stack||error).replace(/Bearer [^\s'"]+/g,'Bearer [redacted]'));process.exit(1)});
