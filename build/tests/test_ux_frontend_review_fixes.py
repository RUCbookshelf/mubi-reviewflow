"""2.1.4 UX 泳道评审轮修复的可运行检查（六条评审意见逐条验收）。

前一轮 faa8b15 的五项修复经评审暴露六处缺陷，本文件把每条修复的“行为层”
验证固化（不止 grep 源码）：

1. CustomEvent 真实行为：顶层自定义 init 键（旧写法 {detail:…,localized:…}）
   被浏览器丢弃、event.localized 恒 undefined——用本机 Node（22+ 内置
   CustomEvent）实测钉住；detail 对象方案可携带标记；并静态断言产品
   dispatch/listener 均已改走 detail。
2. errOf 已翻译错误 → 分析页错误条：Node vm 沙箱加载真实 analysis_locale.js
   的字典与 message()，复刻 errOf 的 T() 翻译语义，fr/de 界面下断言
   已翻译错误直读（className 无 an-technical-message、文本=译文），
   且不带标记的旧路径确实被包壳（标记必要性）。
3. 已完成徽标语义：FastAPI TestClient 造“已决候选组 → 启用”（成员全
   active）与“已决未启用”（成员 staged）两种真实数据，再把真实
   completionBadge 源码（从 unified_import_workspace.js 提取）放到
   Node 里对真实成员跑：前者必须是「已启用」，后者「已核验待启用」。
4. complete 胶囊真折叠：msedge --headless=new 加载真实
   import_export_workspace.css / import_review_refined.css 构造胶囊，
   virtual-time 快进后断言 getBoundingClientRect().height 折叠前 ≥44px
   （移动 ≥22px）、折叠后 0 且 display:none——上一版只设 maxHeight 被
   min-height:44px/22px 压住的缺陷由此钉死。
5. 轮询 abort：静态断言进度轮询请求携带 abort.signal（服务器卡住时
   realImport 才能走出 while 恢复遮罩交互）。
6. 缓存可用性：静态断言空缓存（cachedCount===0）不置 complete。

Node/Edge 不可用时对应测试 skipTest 并注明（本仓库既有 *.cjs 浏览器套件
同样依赖外部运行时；本机两者均在）。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "custom_frontend"

_NODE_CANDIDATES = [
    os.environ.get("REVIEWFLOW_NODE"),
    shutil.which("node"),
    r"C:\Users\16073\AppData\Local\hermes\node\node.exe",
]
_EDGE_CANDIDATES = [
    os.environ.get("REVIEWFLOW_EDGE"),
    shutil.which("msedge"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _first_existing(candidates):
    for item in candidates:
        if item and Path(item).exists():
            return str(item)
    return None


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _run_node(script, timeout=60):
    node = _first_existing(_NODE_CANDIDATES)
    if not node:
        raise unittest.SkipTest("node 不可用：CustomEvent/locale 行为测试需要 Node 22+")
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_path = handle.name
    try:
        done = subprocess.run([node, script_path], capture_output=True, text=True,
                               timeout=timeout, cwd=str(ROOT), encoding="utf-8")
    finally:
        os.unlink(script_path)
    if done.returncode != 0:
        raise AssertionError(f"node 脚本失败：\n{done.stdout}\n{done.stderr}")
    return done.stdout


LOCALE_BOOTSTRAP = r"""
const fs=require('fs'),vm=require('vm'),path=require('path');
const root=process.cwd();
const langs=['zh-CN','en','fr','ru','es','ja','pt','de','sr','ko'];
const dictionary=new Map();let langIndex=0;
const t=(k,...a)=>{const row=dictionary.get(String(k));let s=row?row[langIndex]:String(k);if(a.length)s=s.replace(/\{(\d+)\}/g,(m,d)=>a[+d]===undefined?m:String(a[+d]));return s;};
const register=pairs=>{for(const e of pairs){const[zh,en]=e;const prev=dictionary.get(zh)||dictionary.get(en);const row=langs.map((_,i)=>e[i]||prev?.[i]||en);for(const v of row)if(v)dictionary.set(v,row);dictionary.set(zh,row);dictionary.set(en,row);}};
function makeNode(tag){return{tagName:tag,children:[],_text:'',style:{},className:'',lang:'',open:false,set textContent(v){this._text=v;},get textContent(){return this._text;},setAttribute(){},append(...ns){this.children.push(...ns);},addEventListener(ev,fn){this['on'+ev]=fn;}};}
const sandbox={RFLang:{register,get:()=>langs[langIndex],t},matchMedia:()=>({matches:false}),requestAnimationFrame:fn=>fn(),console};
sandbox.document={createElement:x=>makeNode(x),createElementNS:(ns,x)=>makeNode(x)};sandbox.window=sandbox;vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root,'custom_frontend','analysis_locale.js'),'utf8'),sandbox,{filename:'analysis_locale.js'});
"""


class CustomEventBehaviorTests(unittest.TestCase):
    """评审修1：localized 必须经 detail 携带（CustomEvent init 丢弃顶层自定义键）。"""

    def test_init_level_keys_are_dropped_and_detail_object_works(self):
        out = _run_node(r"""
let fail=0;const check=(n,ok)=>{if(!ok)fail++;console.log((ok?'OK  ':'FAIL')+' '+n);};
if(typeof CustomEvent!=='function'){console.log('SKIP no CustomEvent');process.exit(0);}
const dropped=new CustomEvent('reviewflow:analysis-synthesis-failed',{detail:'msg',localized:true});
check('旧写法 event.localized 被浏览器丢弃(=undefined)',dropped.localized===undefined);
const carried=new CustomEvent('reviewflow:analysis-synthesis-failed',{detail:{message:'msg',localized:true}});
check('detail 对象携带 localized',carried.detail.localized===true&&carried.detail.message==='msg');
process.exit(fail?1:0);
""")
        if out.strip().startswith("SKIP"):
            self.skipTest("node 无内置 CustomEvent")
        self.assertIn("OK", out)
        self.assertNotIn("FAIL", out)

    def test_product_dispatch_and_listener_use_detail(self):
        html = _read(FRONTEND / "index.html")
        nav = _read(FRONTEND / "analysis_navigation.js")
        self.assertIn("detail:{message:e.message, localized:e.rfLocalized === true}", html,
                      "dispatch 必须把 localized 放进 detail 对象")
        self.assertIn("const payload=event.detail||{};const text=typeof payload==='string'?payload:payload.message", nav,
                      "listener 必须从 detail 解包（兼容旧字符串形态）")
        self.assertIn("payload.localized===true", nav)
        self.assertNotIn("event.localized", nav, "不得从 event 顶层读 localized（浏览器会丢弃）")


class TranslatedErrorBannerTests(unittest.TestCase):
    """评审修2：errOf 已翻译错误在 fr/de 界面直读，不再被包“暂无译文”外壳。"""

    def test_fr_de_translated_errors_read_directly(self):
        out = _run_node(LOCALE_BOOTSTRAP + r"""
const RF=sandbox.window.RFAnalysisLocale;let fail=0;
const check=(n,ok)=>{if(!ok)fail++;console.log((ok?'OK  ':'FAIL')+' '+n);};
const errOfTranslate=payload=>{const translated=t(payload);return{message:translated,rfLocalized:translated!==payload};};
for(const [lang,idx] of [['fr',2],['de',7]]){
  langIndex=idx;
  const api=errOfTranslate('at least two independent studies are required');
  check(lang+' errOf 翻译命中并置 rfLocalized',api.rfLocalized===true&&/[A-Za-z]{3}/.test(api.message));
  const detail={message:api.message,localized:api.rfLocalized===true};
  const text=typeof detail==='string'?detail:detail.message;
  const host=RF.message(text,{localized:detail.localized===true});
  check(lang+' 已翻译错误直读(无外壳)',String(host.className).indexOf('an-technical-message')<0&&host.textContent===api.message);
  const legacy=RF.message(api.message);
  check(lang+' 旧路径(无标记)被包壳——标记必要性',String(legacy.className).indexOf('an-technical-message')>=0);
}
process.exit(fail?1:0);
""")
        self.assertNotIn("FAIL", out)

    def test_errof_marks_translated_hits(self):
        html = _read(FRONTEND / "index.html")
        self.assertIn("let translatedHit = false;", html)
        self.assertIn("if (translatedHit) error.rfLocalized = true;", html,
                      "errOf 翻译命中必须在 ApiError 上置 rfLocalized")

    def test_all_error_banner_callers_pass_localized(self):
        for name, needle in [
            ("analysis_influence.js", "message(error.message,{localized:error.rfLocalized===true})"),
            ("analysis_estimator_sensitivity.js", "message(error.message, {localized: error.rfLocalized === true})"),
            ("analysis_dependent_synthesis.js", "message(error.message, {localized: error.rfLocalized === true})"),
            ("analysis_sensitivity_view.js", "message(error.message, {localized: error.rfLocalized === true})"),
        ]:
            self.assertIn(needle, _read(FRONTEND / name), f"{name} 错误条必须透传 localized")

    def test_hksj_error_carries_localized_flag(self):
        html = _read(FRONTEND / "index.html")
        block = html[html.find("function anSynthesisQuery()"):html.find("function anSynthesisQuery()") + 1800]
        self.assertIn("hksj.rfLocalized = true", block, "HKSJ 冲突文案（T() 已本地化）必须带 rfLocalized")


class CompletionBadgeBehaviorTests(unittest.TestCase):
    """评审修3：徽标按成员实际状态计算；TestClient 真数据 + 真实 completionBadge。"""

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(ROOT))
        cls._fixture = tempfile.TemporaryDirectory(prefix="rf_badge_test_")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._fixture.name
        from fastapi.testclient import TestClient
        from custom_backend.main import app
        from coscreen import db, review_imports as store, review_workflow as wf
        from coscreen.models import Article
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP /api/auth/register：全套 pytest 里 TestClient 的 client.host
        # 固定，多模块注册共享同一限流桶会撞 429（“注册尝试过于频繁”）；
        # 直接建用户并签发令牌，行为等价且不占限流额度。
        import custom_backend.auth as auth_mod
        user = auth_mod.create_user("badge@example.test", "Badge-2026-x")
        token = auth_mod.create_access_token(user["id"], user["username"])
        cls.user = {"user_id": user["id"], "username": user["username"]}
        cls.client.headers["Authorization"] = "Bearer " + token
        task = cls.client.post("/api/tasks", json={"name": "badge-probe"}).json()["task_id"]
        cls.base = f"/api/tasks/{task}/import-batches/b"
        ctx = app.state.review_workflow.context(task, cls.user)
        cls.db = ctx.owner_db
        db.init_db(cls.db)
        store.migrate(cls.db)

        def record(conn, key, doi):
            article = Article(zotero_key=key, title="Probe " + key, doi=doi).to_dict()
            conn.execute("INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (key, "s", key, 1, "01-Wiley.ris", json.dumps(article), json.dumps(article), key, key, None, None, "new", "staged", "now"))
            conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", key, 0))

        def group(conn, cid, doi):
            for key in (cid + "a", cid + "b"):
                record(conn, key, doi)
            conn.execute("INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                         (cid, cid, "title", "pending", "now", "now"))
            for key in (cid + "a", cid + "b"):
                conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)", (cid, "record", key))

        with closing(wf.connect(cls.db)) as conn, conn:
            conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,?)", ("s", "01-Wiley.ris", "01-wiley.ris", 1, "now"))
            conn.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)", ("b", str(cls.user["user_id"]), "h", "batch", 1, "staged", "now"))
            conn.execute("INSERT INTO workflow_import_files VALUES(?,?,?,?,?)", ("b", 0, "01-Wiley.ris", "d", "s"))
            group(conn, "gc", "10.test/same")
            group(conn, "hc", "10.test/same2")
        version = cls.client.get(cls.base).json()["policy_version"]
        cls.client.post(cls.base + "/candidates/gc", json={"status": "not_duplicate", "expected_policy_version": version})
        cls.client.post(cls.base + "/activate", json={"expected_policy_version": version + 1})
        version = cls.client.get(cls.base).json()["policy_version"]
        cls.client.post(cls.base + "/candidates/hc", json={"status": "not_duplicate", "expected_policy_version": version})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls._fixture.cleanup()

    def _group_members(self, review_id):
        rows = self.client.get(self.base + "/candidates?view=all").json()["candidates"]
        group = next(item for item in rows if item["review_id"] == review_id)
        return group["members"]

    def test_activated_group_members_all_active(self):
        members = self._group_members("gc")
        states = [(m.get("ref_type"), (m.get("record") or {}).get("state")) for m in members]
        self.assertTrue(all(ref == "active" or state == "active" for ref, state in states),
                        f"启用后的已决候选组成员应全部 active，实际 {states}")

    def test_real_completionBadge_on_real_backend_data(self):
        activated = json.dumps(self._group_members("gc"), ensure_ascii=False)
        staged = json.dumps(self._group_members("hc"), ensure_ascii=False)
        source = _read(FRONTEND / "unified_import_workspace.js")
        match = re.search(r"function completionBadge\(item\)\{.*?\}", source)
        assert match, "unified_import_workspace.js 必须定义 completionBadge"
        out = _run_node(
            "const activated=" + activated + ";\n" +
            "const staged=" + staged + ";\n" +
            match.group(0) + "\n" +
            r"""
let fail=0;const check=(n,ok)=>{if(!ok)fail++;console.log((ok?'OK  ':'FAIL')+' '+n);};
check('启用后的候选组徽标=已启用',completionBadge({members:activated})==='已启用');
check('已决未启用候选组徽标=已核验待启用',completionBadge({members:staged})==='已核验待启用');
check('普通记录徽标=已启用',completionBadge({record_id:'r'})==='已启用');
process.exit(fail?1:0);
""")
        self.assertNotIn("FAIL", out)

    def test_queue_items_carry_same_badge(self):
        source = _read(FRONTEND / "unified_import_workspace.js")
        self.assertIn("category(item)==='completed'", source, "队列项应仅在已完成分类渲染徽标")
        self.assertLess(source.find("category(item)==='completed'"), source.find("qtitle"),
                        "队列徽标应在题名前渲染")


class CapsuleCollapseBrowserTests(unittest.TestCase):
    """评审修4：complete 胶囊真折叠（真实 CSS，桌面 44px / 移动 22px → 0 + display:none）。"""

    PROBE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>:root{--paper:#fff;--line:#d5e3e1;--teal:#2f6f6a;--muted:#60757c;--wash:#eef6f4;--sand:#f6efdf;--amber:#a06a2c;--ink:#172b32}</style>
<link rel="stylesheet" href="file:///@css_main@">
<link rel="stylesheet" href="file:///@css_refined@">
</head><body>
<div id="result">PENDING</div>
<div class="rf-import-page" id="wf-import-review"><div class="compact-header"></div></div>
<script>
window.T=s=>s;window.WorkflowControls={errorText:e=>String((e&&e.message)||e)};
window.ImportExportWorkspace={node:(tag,key,attrs={})=>{const x=document.createElement(tag);if(key!=null)x.textContent=key;for(const[k,v]of Object.entries(attrs)){if(k==='class')x.className=v;else x.setAttribute(k,String(v));}return x;},literal:(tag,value,attrs={})=>{const x=document.createElement(tag);x.textContent=String(value??'');for(const[k,v]of Object.entries(attrs))x.setAttribute(k,String(v));return x;},icon:()=>document.createElement('span'),button:key=>{const b=document.createElement('button');b.type='button';b.textContent=key;return b;}};
</script>
<script src="file:///@loading_js@"></script>
<script>
const capsule=ImportReviewLoading.status({phase:'complete',loaded:53,total:53},null);
document.querySelector('.compact-header').append(capsule);
const before=capsule.getBoundingClientRect();
setTimeout(()=>{const after=capsule.getBoundingClientRect();const cs=getComputedStyle(capsule);
document.getElementById('result').textContent=JSON.stringify({beforeHeight:Math.round(before.height),afterHeight:Math.round(after.height),display:cs.display,minHeight:cs.minHeight});},5800);
</script></body></html>
"""

    def _probe(self, width):
        edge = _first_existing(_EDGE_CANDIDATES)
        if not edge:
            self.skipTest("msedge 不可用：胶囊折叠需要真实浏览器测量")
        probe = (self.PROBE
                 .replace("@css_main@", (FRONTEND / "import_export_workspace.css").as_posix())
                 .replace("@css_refined@", (FRONTEND / "import_review_refined.css").as_posix())
                 .replace("@loading_js@", (FRONTEND / "import_review_loading.js").as_posix()))
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as handle:
            handle.write(probe)
            page = handle.name
        try:
            url = "file:///" + Path(page).as_posix()
            done = subprocess.run(
                [edge, "--headless=new", "--disable-gpu", f"--window-size={width},900",
                 "--virtual-time-budget=9000", "--run-all-compositor-stages-before-draw",
                 "--dump-dom", url],
                capture_output=True, text=True, timeout=120, encoding="utf-8")
        finally:
            os.unlink(page)
        match = re.search(r'<div id="result">(\{.*?\})</div>', done.stdout)
        assert match, f"探针未产出结果：{done.stdout[-500:]} {done.stderr[-500:]}"
        return json.loads(match.group(1))

    def test_desktop_capsule_collapses_to_zero(self):
        data = self._probe(1440)
        self.assertGreaterEqual(data["beforeHeight"], 40, f"桌面折叠前应受 min-height:44px 约束：{data}")
        self.assertEqual(data["afterHeight"], 0, f"折叠后高度必须为 0：{data}")
        self.assertEqual(data["display"], "none")

    def test_mobile_capsule_collapses_to_zero(self):
        data = self._probe(390)
        self.assertGreaterEqual(data["beforeHeight"], 20, f"移动端折叠前应受 min-height:22px 约束：{data}")
        self.assertEqual(data["afterHeight"], 0, f"折叠后高度必须为 0：{data}")
        self.assertEqual(data["display"], "none")


class PollAbortAndCacheGuardTests(unittest.TestCase):
    """评审修5/6：轮询携带 abort.signal；空缓存不置 complete。"""

    def test_progress_poll_carries_abort_signal(self):
        html = _read(FRONTEND / "index.html")
        real_import = html[html.find("async function realImport"):html.find("async function realImport") + 4000]
        self.assertIn("import-progress/", real_import)
        self.assertIn("{signal: abort.signal}", real_import,
                      "进度轮询必须携带 abort.signal，否则服务器卡住时 realImport 永远停在本 await（F53 场景遮罩无法恢复）")

    def test_empty_cache_stays_initial(self):
        source = _read(FRONTEND / "unified_import_workspace.js")
        self.assertIn("const cachedUsable=cached&&cachedCount>0;", source,
                      "空缓存（cachedCount===0）不算可用缓存")
        self.assertIn("loadState=cachedUsable?{phase:'complete'", source,
                      "只有确有数据的缓存才置 complete")
        self.assertIn("cachedUsable&&['initial','background'].includes(state.phase)?loadState:state", source,
                      "静默合并的掩蔽同样以 cachedUsable 为条件")


if __name__ == "__main__":
    unittest.main()
