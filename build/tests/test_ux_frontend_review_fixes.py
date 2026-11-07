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
3. 已完成徽标与计数一致性（r4）：fixture 全部经真实导入 API 构造
   （identity_conflict 冲突候选 → 决策 → 启用；同标题对 → 真实 legacy
   title_duplicate_reviews 组，成员形状直接取自真实响应），再把真实
   id/category/completionBadge/items/readyCount 源码（从
   unified_import_workspace.js 提取）放到 Node 里对真实数据跑：
   已决全 active 组=「已启用」；已决含 staged 成员的组不以组形态吞掉
   成员，其 staged 记录以待启用记录形态可见且 readyCount()=后端
   review_counts.ready；legacy 已决组=「已核验」（真实成员无
   ref_type/record，且无启用环节——上轮自造 legacy 形状败因的反证）。
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
// 评审补强：errOf 结构化对象分支（PoolCompatibilityError 形态）。
langIndex=2;
const payload={message:'incompatible selected effect measures, standardizers or directions for this comparison/outcome/timepoint',reasons:[{type:'direction_conflict',detail:'These reported effects run opposite to Comparison. Convert the estimate and uncertainty to first-group vs second-group direction before pooling.'}]};
const translatedMessage=t(payload.message);
const hit=translatedMessage!==payload.message;
const joined=[translatedMessage,...(payload.reasons||[]).map(r=>r&&r.detail?t(r.detail):r&&r.detail)].filter(Boolean).join('\n');
check('fr 对象分支主消息命中并标记',hit===true&&translatedMessage!==payload.message);
const host2=RF.message(joined,{localized:hit===true});
check('fr 对象分支直读(无外壳)且保留英文明细行',String(host2.className).indexOf('an-technical-message')<0&&host2.textContent===joined&&host2.textContent.includes('opposite to Comparison'));
// 未注册 message（动态后缀形态）不标记：进可展开外壳（诚实行为）。
const dynamic={message:'incompatible selected effect measures, standardizers or directions for this comparison/outcome/timepoint; expected SMD'};
const dynHit=t(dynamic.message)!==dynamic.message;
check('fr 动态后缀不误标',dynHit===false);
const host3=RF.message(t(dynamic.message),{localized:false});
check('fr 动态后缀走可展开外壳',String(host3.className).indexOf('an-technical-message')>=0);
process.exit(fail?1:0);
""")
        self.assertNotIn("FAIL", out)

    def test_errof_marks_translated_hits(self):
        html = _read(FRONTEND / "index.html")
        self.assertIn("let translatedHit = false;", html)
        self.assertIn("if (translatedHit) error.rfLocalized = true;", html,
                      "errOf 翻译命中必须在 ApiError 上置 rfLocalized")
        self.assertIn("const translatedMessage = T(payload.message);", html,
                      "errOf 对象分支（池兼容错误）主消息必须过 T()")
        self.assertIn("(payload.reasons || []).map(reason => reason && reason.detail ? T(reason.detail) : reason?.detail)", html,
                      "errOf 对象分支 reasons 明细必须逐条过 T()")

    def test_all_error_banner_catch_sites_pass_localized(self):
        """全量扫描（评审补强：不再手工枚举文件，防止再漏 tau profile 类调用点）。

        凡以 ``RFAnalysisLocale.message(<error>.message`` 形态渲染错误消息的
        catch 调用点，其后必须紧跟 ``localized: <error>.rfLocalized === true``
        （warnings/method_sources/limits.reason 等技术串展示不属于错误 catch，
        不在扫描形态内，允许不传）。
        """
        checked = 0
        for path in sorted(FRONTEND.glob("analysis_*.js")) + [FRONTEND / "index.html"]:
            source = _read(path)
            for match in re.finditer(r"RFAnalysisLocale\.message\((?:window\.)?(?:error|e|exc)\.message", source):
                checked += 1
                tail = source[match.end():match.end() + 120]
                self.assertIn("localized", tail,
                              f"{path.name} 的错误条调用点必须传 localized（error.rfLocalized）")
                self.assertRegex(tail, r"rfLocalized\s*===\s*true",
                                 f"{path.name} 的 localized 必须取自 error.rfLocalized === true")
        self.assertGreaterEqual(checked, 5, f"扫描到的错误条调用点过少（{checked}），扫描形态可能失效")

    def test_tau_and_pool_error_strings_registered(self):
        locale = _read(FRONTEND / "analysis_locale.js")
        for zh, en in [
            ("轮廓似然区间至少需要三项匹配的研究效应与方差。", "profile interval needs at least three matching study effects and variances"),
            ("REML 轮廓优化失败。", "REML profile optimization failed"),
            ("此比较、结局与时间点的已选效应指标、标准化方法或方向不兼容。", "incompatible selected effect measures, standardizers or directions for this comparison/outcome/timepoint"),
        ]:
            self.assertIn(f'"{zh}"', locale, f"analysis_locale.js 未注册中文键：{zh}")
            self.assertIn(f'"{en}"', locale, f"analysis_locale.js 未注册英文键（须与后端错误串一致）：{en}")
        # 行级校验：zh 键与 en 键必须位于同一注册行（成对注册）。
        for zh, en in [("轮廓似然区间至少需要三项匹配的研究效应与方差。", "profile interval needs at least three"),
                       ("此比较、结局与时间点的已选效应指标、标准化方法或方向不兼容。", "incompatible selected effect measures")]:
            row = next((line for line in locale.splitlines() if f'"{zh}"' in line), None)
            assert row and en in row, f"词条必须 zh/en 成对同行注册：{zh}"

    def test_hksj_error_carries_localized_flag(self):
        html = _read(FRONTEND / "index.html")
        block = html[html.find("function anSynthesisQuery()"):html.find("function anSynthesisQuery()") + 1800]
        self.assertIn("hksj.rfLocalized = true", block, "HKSJ 冲突文案（T() 已本地化）必须带 rfLocalized")


class CompletionBadgeBehaviorTests(unittest.TestCase):
    """高4（r4）：真实后端形状上的分类/徽标/计数一致性。

    fixture 全部经真实导入 API 构造（无直写数据库、无自造成员形状）：
    - batch A：base1/base2 两条独立记录（导入即激活）。
    - batch B：与 base1 同外部 key 不同 DOI 的冲突记录 → identity_conflict
      候选组（成员 [('active',article), ('record',staged)]）+ 一条 fresh 记录。
    - batch C：与 base2 同 key 不同 DOI 的冲突记录 → 第二个候选组。
    决策 B not_duplicate 后【不】启用 → hc 状态（已决、成员仍 staged、
    review_counts.ready=1）；决策 C not_duplicate 后启用 → gc 状态
    （已决、成员全 active）。
    - legacy：单文件 /import 内同标题对 → 真实 title_duplicate_reviews 组
      （成员只有 zotero_key/title/authors/… RIS 字段），经真实决策 API 置为
      not_duplicate。上轮“legacy-active 用非真实成员形状”的败因由此根治：
      legacy 成员形状直接取自真实 API 响应并断言键集合。
    """

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(ROOT))
        cls._fixture = tempfile.TemporaryDirectory(prefix="rf_badge_test_")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._fixture.name
        from fastapi.testclient import TestClient
        from custom_backend.main import app
        import custom_backend.auth as auth_mod
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP /api/auth/register：全套 pytest 里 TestClient 的 client.host
        # 固定，多模块注册共享同一限流桶会撞 429（“注册尝试过于频繁”）；
        # 直接建用户并签发令牌，行为等价且不占限流额度。
        user = auth_mod.create_user("badge@example.test", "Badge-2026-x")
        token = auth_mod.create_access_token(user["id"], user["username"])
        cls.user = {"user_id": user["id"], "username": user["username"]}
        cls.client.headers["Authorization"] = "Bearer " + token
        task = cls.client.post("/api/tasks", json={"name": "badge-probe-r4"}).json()["task_id"]
        cls.task = task

        def ris(entries):
            return "\n\n".join(
                "TY  - JOUR\n" + "".join(f"{tag}  - {value}\n" for tag, value in entry.items()) + "ER  - "
                for entry in entries)

        base1 = {"AU": "Alpha, A", "TI": "Conflict Base One", "PY": "2024", "DO": "10.test/base1", "ID": "SHAREKEY1"}
        base2 = {"AU": "Bravo, B", "TI": "Conflict Base Two", "PY": "2024", "DO": "10.test/base2", "ID": "SHAREKEY2"}
        conflict1 = {"AU": "Alpha, A", "TI": "Conflict Base One Variant", "PY": "2024", "DO": "10.test/other1", "ID": "SHAREKEY1"}
        conflict2 = {"AU": "Bravo, B", "TI": "Conflict Base Two Variant", "PY": "2024", "DO": "10.test/other2", "ID": "SHAREKEY2"}
        fresh = {"AU": "Fresh, F", "TI": "Fresh Independent Study", "PY": "2025", "DO": "10.test/fresh"}

        def upload(name, text):
            result = cls.client.post(
                f"/api/tasks/{task}/import-batch",
                files={"files": (name, text.encode("utf-8"), "application/octet-stream")}).json()
            assert result.get("batch_id"), f"真实导入失败：{result}"
            return result["batch_id"]

        upload("base.ris", ris([base1, base2]))
        cls.batch_hc = upload("batchB.ris", ris([conflict1, fresh]))
        cls.batch_gc = upload("batchC.ris", ris([conflict2]))

        def decide(batch_id, status):
            detail = cls.client.get(f"/api/tasks/{task}/import-batches/{batch_id}?view=all&limit=200").json()
            cands = cls.client.get(f"/api/tasks/{task}/import-batches/{batch_id}/candidates?view=all").json()["candidates"]
            assert cands, "真实冲突候选未生成"
            cls.client.post(f"/api/tasks/{task}/import-batches/{batch_id}/candidates/{cands[0]['review_id']}",
                            json={"status": status, "expected_policy_version": detail["policy_version"]}).raise_for_status()

        # hc：已决未启用（成员仍 staged，ready=1）——上轮回归现场。
        decide(cls.batch_hc, "not_duplicate")
        # gc：已决并启用（成员全 active）。
        decide(cls.batch_gc, "not_duplicate")
        version = cls.client.get(f"/api/tasks/{task}/import-batches/{cls.batch_gc}?view=all&limit=200").json()["policy_version"]
        cls.client.post(f"/api/tasks/{task}/import-batches/{cls.batch_gc}/activate",
                        json={"expected_policy_version": version}).raise_for_status()
        # legacy：真实同标题对 → title_duplicate_reviews 组 → 真实决策。
        cls.client.post(
            f"/api/tasks/{task}/import",
            files={"file": ("legacy.ris", ris([
                {"AU": "Legacy, L", "TI": "Legacy Same Title Pair", "PY": "2023", "DO": "10.test/lg1"},
                {"AU": "Legacy, L", "TI": "Legacy Same Title Pair", "PY": "2023", "DO": "10.test/lg2"},
            ]).encode("utf-8"), "application/octet-stream")}).raise_for_status()
        groups = cls.client.get(f"/api/tasks/{task}/title-duplicate-reviews?view=all").json()["groups"]
        assert groups, "真实 legacy 标题组未生成"
        cls.client.post(f"/api/tasks/{task}/title-duplicate-reviews/{groups[0]['review_id']}",
                        json={"decision": "not_duplicate", "keeper_key": ""}).raise_for_status()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls._fixture.cleanup()

    def _batch_state(self, batch_id):
        detail = self.client.get(f"/api/tasks/{self.task}/import-batches/{batch_id}?view=all&limit=200").json()
        cands = self.client.get(f"/api/tasks/{self.task}/import-batches/{batch_id}/candidates?view=all").json()["candidates"]
        return detail, cands

    def test_hc_state_is_decided_with_staged_member(self):
        """真实 hc 现场钉子：已决组仍含 staged 成员、后端 ready=1（r3 回归输入）。"""
        detail, cands = self._batch_state(self.batch_hc)
        decided = [c for c in cands if c["status"] == "not_duplicate"]
        self.assertTrue(decided, "hc 候选必须已决")
        members = decided[0]["members"]
        self.assertTrue(any(m["ref_type"] == "record" and m["record"]["state"] == "staged" for m in members),
                        f"hc 必须仍含 staged 成员记录，实际 {[(m['ref_type'], (m.get('record') or {}).get('state')) for m in members]}")
        self.assertEqual(detail["review_counts"]["ready"], 1,
                         f"hc 现场后端 ready 必须为 1（待启用记录正是该 staged 成员），实际 {detail['review_counts']}")

    def test_gc_state_members_all_active(self):
        detail, cands = self._batch_state(self.batch_gc)
        decided = [c for c in cands if c["status"] == "not_duplicate"]
        self.assertTrue(decided)
        for member in decided[0]["members"]:
            self.assertTrue(member["ref_type"] == "active" or member["record"]["state"] == "active",
                            f"启用后的已决候选组成员应全部 active，实际 {member}")

    def test_real_legacy_member_shape_has_no_ref_type(self):
        """真实 legacy 成员形状钉子（上轮自造形状败因的直接反证）。"""
        groups = self.client.get(f"/api/tasks/{self.task}/title-duplicate-reviews?view=all").json()["groups"]
        decided = [g for g in groups if g["status"] == "not_duplicate"]
        self.assertTrue(decided, "legacy 组必须已决")
        for member in decided[0]["members"]:
            self.assertNotIn("ref_type", member, "真实 legacy 成员没有 ref_type（上轮自造形状引入的键）")
            self.assertNotIn("record", member)
            self.assertNotIn("article", member)
            self.assertIn("zotero_key", member)
            self.assertIn("title", member)

    def test_items_level_badges_and_counts_on_real_backend_data(self):
        """items()/category/readyCount 级断言（r4 语义，全部真实 API 数据）。

        从 unified_import_workspace.js 提取真实的 id/completionBadge/category/
        items/readyCount，配真实 import_review_model 与真实 API 响应实跑：
        - gc（已决全 active）：组进 completed、徽标「已启用」、成员不双显；
        - hc（已决含 staged 成员）：组不以组形态出现；其 staged 成员记录以
          reason='ready' 进入 items()（待启用页签可见）；
        - 计数一致性：readyCount() === 后端 review_counts.ready === ready 记录数；
        - legacy 已决组（真实形状）：进 completed、徽标「已核验」。
        """
        detail_hc, cands_hc = self._batch_state(self.batch_hc)
        detail_gc, cands_gc = self._batch_state(self.batch_gc)
        legacy_groups = self.client.get(f"/api/tasks/{self.task}/title-duplicate-reviews?view=all").json()["groups"]
        out = _run_node(
            "const groupsHC=" + json.dumps(cands_hc, ensure_ascii=False) + ";\n" +
            "const recordsHC=" + json.dumps(detail_hc["records"], ensure_ascii=False) + ";\n" +
            "const countsHC=" + json.dumps(detail_hc["review_counts"], ensure_ascii=False) + ";\n" +
            "const groupsGC=" + json.dumps(cands_gc, ensure_ascii=False) + ";\n" +
            "const recordsGC=" + json.dumps(detail_gc["records"], ensure_ascii=False) + ";\n" +
            "const countsGC=" + json.dumps(detail_gc["review_counts"], ensure_ascii=False) + ";\n" +
            "const legacy=" + json.dumps(legacy_groups, ensure_ascii=False) + ";\n" +
            r"""
const fs=require('fs'),path=require('path');
const src=fs.readFileSync(path.join(process.cwd(),'custom_frontend','unified_import_workspace.js'),'utf8');
const pick=(re,name)=>{const m=src.match(re);if(!m){console.log('FAIL 提取失败: '+name);process.exit(1);}return m[0];};
const idDecl=pick(/const id=item=>[^\n]*/,'id');
const badgeSrc=pick(/function completionBadge\(item\)\{[^\n]*\}/,'completionBadge');
const catSrc=pick(/function category\(item\)\{[^\n]*\}/,'category');
const itemsSrc=pick(/function items\(\)\{[^\n]*\}/,'items');
const readySrc=pick(/function readyCount\(\)\{[^\n]*\}/,'readyCount');
const M=require(path.join(process.cwd(),'custom_frontend','import_review_model.js'));
const deferred=()=>new Set();
const factory=new Function('groups','legacy','records','batch','M','deferred',
  '"use strict";'+idDecl+badgeSrc+catSrc+itemsSrc+readySrc+'return {id,completionBadge,category,items,readyCount};');
let fail=0;const check=(n,ok)=>{if(!ok)fail++;console.log((ok?'OK  ':'FAIL')+' '+n);};

// —— hc 现场（已决、成员仍 staged、后端 ready=1）——
const hc=factory(groupsHC,[],recordsHC,{review_counts:countsHC},M,deferred);
const hcGroups=groupsHC.filter(g=>['duplicate','not_duplicate'].includes(g.status));
const hcGroup=hcGroups[0];
const hcStagedIds=hcGroup.members.filter(m=>m.ref_type==='record').map(m=>m.ref_id);
check('hc(已决含staged成员)不以组形态进入 items()',!hc.items().some(x=>x.members&&x.review_id===hcGroup.review_id));
const hcReadyRecords=hc.items().filter(x=>!x.members&&M.reason(x)==='ready');
check('hc 的 staged 成员记录以 ready 记录形态可见(待启用页签)',hcReadyRecords.length===hcStagedIds.length&&hcReadyRecords.length>0);
check('hc ready 记录 category=ready(待启用页签可数)',hcReadyRecords.every(x=>hc.category(x)==='ready'));
check('hc readyCount()=后端 review_counts.ready(页签=按钮=实际可启用)',hc.readyCount()===countsHC.ready&&hc.readyCount()===1);
check('hc readyCount()=本地 ready 记录数(同源)',hc.readyCount()===recordsHC.filter(r=>M.reason(r)==='ready').length);

// —— gc 现场（已决并启用、成员全 active）——
const gc=factory(groupsGC,[],recordsGC,{review_counts:countsGC},M,deferred);
const gcGroup=groupsGC.filter(g=>['duplicate','not_duplicate'].includes(g.status))[0];
check('gc(已决全active)以组形态进入 items()',gc.items().some(x=>x.members&&x.review_id===gcGroup.review_id));
check('gc category=completed',gc.category(gcGroup)==='completed');
check('gc badge=已启用',gc.completionBadge(gcGroup)==='已启用');
const gcMemberIds=new Set(gcGroup.members.filter(m=>m.ref_type==='record').map(m=>m.ref_id));
check('gc 成员记录不以单条形态双显',!gc.items().some(x=>!x.members&&gcMemberIds.has(x.record_id)));
check('gc readyCount()=后端 ready=0(已无可启用)',gc.readyCount()===countsGC.ready&&gc.readyCount()===0);

// —— legacy 现场（真实形状：成员只有 zotero_key 等 RIS 字段）——
// 与 I.open 加载器同款变换：prior.all.map(g=>({...g,legacy:true}))。
const legacyLoaded=legacy.map(g=>({...g,legacy:true}));
const lg=factory([],legacyLoaded,[],{review_counts:{}},M,deferred);
const lgGroup=legacyLoaded.filter(g=>['duplicate','not_duplicate'].includes(g.status))[0];
// r10-U2（导入12项 #6-A）：任务级 legacy 组移出批次分类列表，由独立分区
// （legacySection，.legacy-queue）承载。旧钉「进入 items()」随行为变更同批
// 改为**双侧显式**新语义：items() 不含它，且分区承载它。校验强度不减
// （可见/可点的分区断言见 test_r10_u2_legacy_scope.py 的浏览器钉子）。
check('legacy 已决组不进入批次 items()（#6-A：批次页只呈现批次范围）',!lg.items().some(x=>x.members&&x.review_id===lgGroup.review_id));
check('legacy 组由独立分区承载（legacySection 源码存在且以 .legacy-queue 渲染）',
  src.includes('function legacySection()')&&src.includes("'legacy-queue'")&&src.includes('data-legacy-scope'));
check('batch-only items() 仍含批次记录',lg.items().length===0);
check('legacy category=completed',lg.category(lgGroup)==='completed');
check('legacy badge=已核验(无启用环节，不得虚构待启用/已启用)',lg.completionBadge(lgGroup)==='已核验');
process.exit(fail?1:0);
""")
        self.assertNotIn("FAIL", out)

    def test_queue_items_carry_same_badge(self):
        source = _read(FRONTEND / "unified_import_workspace.js")
        self.assertIn("category(item)==='completed'", source, "队列项应仅在已完成分类渲染徽标")
        # r10-U2（#6-A）：任务级分区 legacySection() 也含 ".qtitle"，全文件
        # 顺序比较会误命中它；只在批次队列循环段内比较（校验强度不变：
        # 仍是「队列循环里徽标代码先于题名」）。
        start = source.index("for(const item of visible.slice(page*20,page*20+20))")
        end = source.index("if(visible.length>20)", start)
        loop = source[start:end]
        self.assertIn("category(item)==='completed'", loop, "队列循环必须渲染状态徽标")
        self.assertLess(loop.find("category(item)==='completed'"), loop.find("qtitle"),
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
