# -*- coding: utf-8 -*-
"""r7-frontsec 泳道（前端缓存/登出/词典）钉子。

覆盖第五轮外部审查 + deepseek 复现复审（out/external_reviews/verif5_groupA.md、
minimax_full_audit_5.md）确认的四条：

- MM5-01：custom_frontend/index.html 的 ?v= 缓存令牌与引用文件内容脱钩
  （复审核实 7 个真过期，旗舰 ai_suggest.js 的 2.1.4-ai1 在 r7-aiout 改动后未
  bump；build/launcher.py 对非 HTML 静态资源下发 max-age=86400 使脱钩窗口放大到
  24h）。钉子：对 index.html 全部 ?v= 令牌，用 git 历史比对“令牌最后一次引入
  提交”与“文件最后一次内容提交”，文件在令牌引入之后又变过即红（离线 git，无
  网络）。回退修复（把令牌改回旧值）必须变红。
- MM5-02：写请求 401 时统一登出被“未保存改动”守卫拦死——失败的写仍计入
  apiPendingWrites → guard() 的 pendingWork() 恒真 → logout 直接 return，登录页
  永不出现。钉子：node 直跑从 index.html 原文抽取的 api/apiRequest/logout +
  与 workflow_controls.js:64/466-468 同构的 guard 桩，断言 POST 401 完成登出；
  GET 401 干净会话仍走守卫并登出；GET 401 带草稿仍弹守卫（行为不变）。
- MM5-03：十语言词典同中文键冲突注册（复审实测 387 个重复键 / 147 个英文互异
  冲突，胜者由 script 顺序决定，旗舰『页码』全语言显示为 Pages）。三件套：
  (a) 回放真实注册断言英文互异冲突键 == 措辞类白名单（概念级冲突必须为 0，
      白名单条目计数入报告且必须仍然真实冲突，防止白名单腐烂）；
  (b) 9 个复审点名键 + 重试/确认重复 的胜者英文逐一钉死；
  (c) register() 对同 zh 键重复注册不同行 console.warn（行为不变）。
- MM5-17：多语言引擎注释『9 语言』改『10 语言』（含塞尔维亚语 sr）。
"""

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
INDEX = FRONTEND / "index.html"

NODE_CANDIDATES = (
    r"C:\Users\16073\AppData\Local\hermes\node\node.exe",
)


def _node_exe():
    for exe in (*NODE_CANDIDATES, shutil.which("node")):
        if exe and pathlib.Path(exe).is_file():
            return str(exe)
    return None


def _run_node(script, timeout=90):
    exe = _node_exe()
    if exe is None:
        raise unittest.SkipTest("本机无 node 可执行文件（JS 行为级验证不可用）")
    with subprocess.Popen(
        [exe, "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as proc:
        out, err = proc.communicate(script.encode("utf-8"), timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 退出码 %d：%s" % (proc.returncode, err.decode("utf-8", "replace")[-2000:]))
    return out.decode("utf-8", "replace")


# =====================================================================
# MM5-01：?v= 令牌与文件内容绑定（git 历史，离线）
# =====================================================================

WORKTREE = "__WORKTREE__"  # 比一切已提交都新的哨兵（工作树未提交改动）


def _git(args):
    proc = subprocess.run(
        ["git"] + args, cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError("git %s 失败：%s" % (" ".join(args[:3]), proc.stderr[:400]))
    return proc.stdout


def _parse_tokens(html):
    """index.html 中全部 ?v= 引用（script src + link href）。

    路径可带可不带 ./ 前缀（index.html:9151-9152 的两个 <link href="xxx.css?v=">
    就不带），统一按 custom_frontend/ 下的相对文件名归一化。
    """
    return re.findall(r'(?:src|href)="(?:\./)?([^"?]+)\?v=([^"]+)"', html)


class CacheTokenBindingTests(unittest.TestCase):
    """MM5-01 钉子：令牌引入提交必须不早于文件最后内容提交。

    机制：launcher 对非 HTML 静态资源下发 public, max-age=86400，?v= 令牌是唯一
    失效手段；令牌未随内容 bump = 升级用户 24h 内旧 JS 配新后端。
    """

    def test_token_introduced_no_earlier_than_assets_last_content_change(self):
        html = INDEX.read_text(encoding="utf-8")
        pairs = _parse_tokens(html)
        self.assertTrue(pairs, "index.html 未解析到任何 ?v= 令牌")
        # 解析完整性（r1 评审修正）：页面里出现的 *每一个* ?v=（无论 ./ 前缀与否）
        # 都必须被上面的 src/href 正则捕获，否则该资产的脱钩将无人看守
        self.assertEqual(
            len(pairs), html.count("?v="),
            "令牌解析不完整：页面共有 %d 处 ?v=，src/href 正则只捕获 %d 处"
            % (html.count("?v="), len(pairs)),
        )
        attr_refs = re.findall(r'(?:src|href)="(?:\./)?([A-Za-z0-9_./-]+)\?v=', html)
        self.assertEqual(
            sorted(attr_refs), sorted(a for a, _ in pairs),
            "令牌解析不完整（存在未被 src/href 捕获的 ?v= 引用）",
        )
        per_asset = {}
        for asset, token in pairs:
            per_asset.setdefault(asset, set()).add(token)
        double = {a: t for a, t in per_asset.items() if len(t) > 1}
        self.assertFalse(double, "同一文件被引用了多个不同令牌：%r" % double)
        missing = [a for a in per_asset if not (FRONTEND / a).is_file()]
        self.assertFalse(missing, "index.html 引用了不存在的文件：%r" % missing)

        # 1) 主线提交序（first-parent：线性序；侧支 blob 不参与回放，避免侧支上
        #    “令牌短暂消失又随合并复现”把引入点伪装成很新的提交）
        order = _git(["rev-list", "--first-parent", "HEAD"]).split()
        rank = {c: i for i, c in enumerate(order)}

        def rank_of(commit):
            return -1 if commit == WORKTREE else rank[commit]

        # 2) 沿主线逐提交回放 index.html，找每个 (asset, token) 最后一次
        #    “出现”（父提交无 → 本提交有）的提交
        hist = _git(["log", "--first-parent", "--format=%H", "--", "custom_frontend/index.html"]).split()
        intro = {}
        prev = set()
        for commit in sorted(hist, key=lambda c: -rank[c]):  # 旧→新（rank 大者旧）
            blob = _git(["show", "%s:custom_frontend/index.html" % commit])
            current = set(_parse_tokens(blob))
            for pair in current - prev:
                intro[pair] = commit
            prev = current
        head_html = _git(["show", "HEAD:custom_frontend/index.html"])
        head_pairs = set(_parse_tokens(head_html))

        # 3) 每个资产最后一次内容提交（--name-only 全史一遍；提交行加 "C " 前缀，
        #    防合并提交无文件列表时把文件行误当提交行）
        asset_last = {}
        current = None
        for raw in _git(["log", "--first-parent", "--format=C %H", "--name-only"]).splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("C "):
                current = line[2:]
                continue
            path = line.strip('"')
            if " -> " in path:
                path = path.split(" -> ")[-1].strip('"')
            if path.startswith("custom_frontend/"):
                rel = path[len("custom_frontend/"):]
                if rel not in asset_last:  # log 新→旧，首个即最新
                    asset_last[rel] = current

        # 4) 工作树漂移：git status 报告的改动/新增文件 → 视为“又改过”（比一切提交新）
        #    （不能按字节比 blob 哈希：Windows autocrlf 会让全部文件都“不等”）
        for line in _git(["status", "--porcelain", "--", "custom_frontend/"]).splitlines():
            if not line.strip():
                continue
            path = line[3:].strip().strip('"')
            if " -> " in path:  # 重命名取新路径
                path = path.split(" -> ")[-1]
            if path.startswith("custom_frontend/"):
                asset_last[path[len("custom_frontend/"):]] = WORKTREE

        stale = []
        for asset, token in sorted(pairs):
            pair = (asset, token)
            if pair in intro:
                intro_commit = intro[pair]
            elif pair not in head_pairs:
                intro_commit = WORKTREE  # 令牌只存在于工作树（未提交的新 bump）
            else:
                self.fail("令牌 %s?v=%s 在 HEAD 中存在但历史回放未捕获" % pair)
            last = asset_last.get(asset, WORKTREE)
            # rank 越小越新：文件最后内容变更比令牌引入更新（rank 更小）即脱钩
            if rank_of(last) < rank_of(intro_commit):
                stale.append(
                    "%s?v=%s：文件最后内容变更（%s）晚于令牌最后一次引入（%s），"
                    "必须 bump 令牌后重新提交"
                    % (asset, token,
                       last if last == WORKTREE else last[:10],
                       intro_commit if intro_commit == WORKTREE else intro_commit[:10])
                )
        self.assertEqual(
            stale, [],
            "MM5-01：?v= 令牌与内容脱钩（升级用户 24h 内旧 JS 配新后端）：\n"
            + "\n".join(stale),
        )


# =====================================================================
# MM5-02：写请求 401 强制登出（node 直跑抽取函数）
# =====================================================================

def _extract_function(html, pattern, name):
    m = re.search(pattern, html, re.S)
    if not m:
        raise AssertionError("index.html 中未找到函数 %s" % name)
    return m.group(0)


_LOGOUT_HARNESS = r"""
'use strict';
const log = { toasts: [], guardCalls: 0, dialogShown: 0 };
let draftDirty = false;
let apiPendingWrites = 0;
let AUTH_TOKEN = 'probe-token';
const TOKEN_KEY = 'AUTH_TOKEN';
const API_BASE = '';
function toast(msg) { log.toasts.push(String(msg)); }
function T(s) { return s; }
function closeTeamMemberMenus() {}
function closeEvents() {}
const S = { taskVersion: 0 };
let screenRequestVersion = 0, alRankVersion = 0, progressVersion = 0, analysisLoadGeneration = 0;
let analysisLoadTimer = null;
let anStudies = null, anEffects = null, anArticles = null;
const FtPdf = { close() {} }, CbPdf = { close() {} };
function setMobileNav() {}
function makeEl() {
  return { style: {}, replaceChildren() {}, removeAttribute() {}, getAttribute() { return null; },
           value: '', hidden: false, inert: false };
}
const authScreen = makeEl();
function $(sel) { return sel === '#authScreen' ? authScreen : makeEl(); }
const document = { dispatchEvent() {}, querySelector() { return makeEl(); }, body: {} };
const sessionStorage = { removeItem() {} };
/* 与 workflow_controls.js:64 的 pendingWork() 同构（共享被抽取 api() 维护的
   apiPendingWrites 计数）；guard 前两检同 workflow_controls.js:466-468。 */
function pendingWork() {
  return apiPendingWrites > 0 || window.WorkspaceUndo?.isBusy()
      || window.WorkflowEvidence?.uploading() || window.WorkflowExtraction?.saving();
}
const guard = async () => {
  log.guardCalls++;
  if (pendingWork()) { log.toasts.push('Wait for the current save or upload to finish.'); return false; }
  if (!draftDirty) return true;
  log.dialogShown++;  /* 草稿守卫对话框：用户选“留在此处”→ false（原行为） */
  return false;
};
const window = { WorkflowControls: { guard } };
globalThis.WorkflowControls = window.WorkflowControls;
const fetch = async () => ({ status: 401 });

__EXTRACTED__

async function run(method, dirty) {
  apiPendingWrites = 0; AUTH_TOKEN = 'probe-token'; authScreen.style.display = 'none';
  log.toasts.length = 0; log.guardCalls = 0; log.dialogShown = 0; draftDirty = dirty;
  let thrown = null;
  try { await api('/api/tasks/1/decisions', { method, body: { a: 1 } }); }
  catch (e) { thrown = e && e.status; }
  await new Promise(r => setTimeout(r, 0));
  return { loggedOut: authScreen.style.display === 'flex', thrown,
           guardCalls: log.guardCalls, dialogShown: log.dialogShown,
           pendingAfter: apiPendingWrites, toasts: log.toasts.slice() };
}
(async () => {
  const out = {
    post401: await run('POST', false),
    post401_draft: await run('POST', true),
    get401: await run('GET', false),
    get401_draft: await run('GET', true),
  };
  process.stdout.write(JSON.stringify(out));
})();
"""


class ForcedLogoutOn401Tests(unittest.TestCase):
    """MM5-02 钉子：POST 401 完成登出；GET 401 守卫路径不变。"""

    def test_post_401_forces_logout_and_get_401_keeps_guard(self):
        html = INDEX.read_text(encoding="utf-8")
        extracted = "\n".join([
            _extract_function(html, r"\nclass ApiError extends Error \{.*?\n\}", "ApiError"),
            _extract_function(html, r"\nasync function api\(path, opts = \{\}\) \{.*?\n\}", "api"),
            _extract_function(html, r"\nasync function apiRequest\(path, opts = \{\}\) \{.*?\n\}", "apiRequest"),
            _extract_function(html, r"\nasync function logout\(msg[^)]*\) \{.*?\n\}", "logout"),
        ])
        self.assertIn("resp.status === 401", extracted)
        script = _LOGOUT_HARNESS.replace("__EXTRACTED__", extracted)
        result = json.loads(_run_node(script).strip().splitlines()[-1])

        post = result["post401"]
        self.assertEqual(post["thrown"], 401)
        self.assertTrue(
            post["loggedOut"],
            "MM5-02：POST 401 未回到登录页（guard 被在途写计数拦死）。toasts=%r" % post["toasts"],
        )
        self.assertEqual(post["pendingAfter"], 0, "失败写必须最终从 apiPendingWrites 减出")
        # 写 401 强制登出不依赖草稿状态
        self.assertTrue(result["post401_draft"]["loggedOut"], "带草稿的 POST 401 同样必须强制登出")

        get = result["get401"]
        self.assertEqual(get["thrown"], 401)
        self.assertTrue(get["loggedOut"], "GET 401 干净会话保持登出（回归）")
        self.assertEqual(get["guardCalls"], 1, "GET 401 仍走守卫")

        gd = result["get401_draft"]
        self.assertFalse(
            gd["loggedOut"],
            "GET 401 带草稿时守卫对话框拦截登出（原行为，回归钉）",
        )
        self.assertEqual(gd["guardCalls"], 1)
        self.assertEqual(gd["dialogShown"], 1, "GET 401 带草稿必须弹守卫对话框（原行为）")


# =====================================================================
# MM5-03：词典冲突回放 + register() 冲突告警
# =====================================================================

# 概念级冲突键（两个不同概念共用一个中文源串，或同概念错译）：必须修到 0。
CONCEPT_KEYS = frozenset((
    "页码", "说明", "注册库", "暂不可用", "填空", "文献类型", "导入", "自动",
    "来源范围",  # 复审点名 9 例
    "重试", "确认重复",  # 同类概念级冲突（Retry vs Retry loading；动作 vs 状态）
))

# 复审点名的旗舰键修复后应有的英文胜者
EXPECTED_WINNERS = {
    "页码": "Page number",          # 不再是 ai_suggest.js 的 Pages
    "说明": "Note",                 # 不再被 refined 的 Help 覆盖
    "注册库": "Registry",           # PRISMA 数据源类型，不再显示动词 Register
    "暂不可用": "Temporarily unavailable",
    "填空": "Free text",            # 编码维度输入类型；轨迹侧改键“短文本”
    "文献类型": "Article type",      # screen_reader 的引用元数据；导入侧改键“记录类型”
    "导入": "Import",               # 导入历史的状态概念改键“已导入”→ Imported
    "自动": "Automatic",
    "来源范围": "Source scope",
    "重试": "Retry",                # “重试加载”拆键后通用键只余 Retry
    "确认重复": "Confirm duplicate",  # 状态概念由“已确认重复”承担
}

# 措辞类冲突白名单：同一概念的不同英文措辞/大小写/单复数（胜者=script 顺序，语义均成立）。
# 白名单必须与回放结果精确相等：新冲突即红（要求修复或登记），条目失效也红（防白名单腐烂）。
# 措辞类保留计数登记值（收敛后须同步下调并移除条目）：147 - 11 概念级 = 136
EXPECTED_WORDING_CONFLICT_COUNT = 136
WORDING_ALLOWLIST = frozenset((
    "Begg 检验的常规渐近 p 值可能校准不佳。漏斗图不对称或失效安全数不能证明发表偏倚，也不能解释研究缺失原因。",
    "CER 是从输入效应值以外取得的假设；更换 CER 会改变换算后的 NNT。",
    "Card 书中算例使用原始 r；FISHER_Z 分析的目标值和假定缺失值必须用 Fisher z，不能直接套用书中的 r 阈值。",
    "DL τ² 截断为零；稳健区间可应对方差设定错误，但不能消除真实的研究间异质性。",
    "DerSimonian–Laird 随机效应",
    "Fisher z 尺度",
    "H 与 I² 转换区间至少需要三项研究。",
    "Higgins 与 Thompson（2002）τ² 转换区间",
    "Higgins 与 Thompson（2002）τ² 转换区间（H 界限经典型方差 (k−1)/C 映射）",
    "N>5k+10 是任意的经验阈值，不是有效性标准（Rosenthal 1991；见 Rosenberg 2005，第 466 页）。",
    "NNT 取决于输入的对照组事件风险（CER）；使用前请按目标人群实际 CER 重新计算。",
    "PET 将效应对 SE 回归；PEESE 将效应对 SE² 回归；两者均使用 1/SE² 权重。",
    "Profile REML τ² 截断为零；稳健区间可应对方差设定错误，但不能消除真实的研究间异质性。",
    "RD 为治疗组风险减对照组风险；正值表示事件风险增加（伤害，NNTH），负值表示风险降低（获益，NNTB）。",
    "REML τ² 轮廓区间目前仅支持 95% 水平。",
    "REML 随机效应",
    "Viechtbauer（2007）τ² Q-profile 区间",
    "netmeta::rankogram / netrank(method=\"SUCRA\") 从拟合协方差矩阵的对角线抽取独立正态效应，并使用 netmeta 自身的 τ² 口径；本模块从 network_random 完整的参考组编码协方差中抽样。只有拟合相关性可忽略时，两者才会在蒙特卡洛误差范围内一致。",
    "一个不一致配对格为零，Wald 方差依据的信息很少；Bonett–Price 和 Newcombe 区间更适合此类表格。",
    "一个不一致配对格为零，已对两个不一致格各加 0.5。校正结果仅供敏感性分析，不应作为主要结果。",
    "一个不一致配对格为零，配对优势比可用的信息很少；该区间可能不可靠。合并时优先考虑精确条件区间。",
    "不明确",
    "不是重复，全部保留",
    "两组均无事件或均为全事件；根据 Cochrane 方法，这类表格不能为相对效应提供信息。任何校正后数值仅供敏感性分析。",
    "亚组差异检验在各亚组内共用一个 DL τ²。亚组应在综述方案中预先定义，且每项研究只能归入一个亚组（Cochrane Handbook §10.11）。",
    "优势比不可折叠；即使 OR 不变，NNT 仍随 CER 改变。只应对目标人群适用的 CER 报告 NNT。",
    "估计值有限，但抽样方差为零，无法用于逆方差加权。",
    "低",
    "使用固定效应临界值：z 选项采用双侧标准正态分位数（metafor 惯例）；t 选项采用 Rosenberg 的 Student t 分位数，并迭代求解自由度 k+N−1。",
    "修正 HKSJ 区间用于随机效应模型；主合并分析不接受固定效应 + HKSJ。此处仅供并列敏感性比较。",
    "修正 Hartung–Knapp–Sidik–Jonkman t 区间",
    "修正 Knapp–Hartung 使用 max(1，残差 Q/自由度) 调整尺度，并采用 t 分布。",
    "假定每项缺失研究的 Z 值恰为 0；显著性水平为双侧 α=0.05。",
    "假定每项缺失研究的效应值都等于所填的缺失研究平均效应值；目标值和假定值都使用分析尺度。",
    "假定缺失研究的平均效应恰为零、平均权重为 Σw/k；该数值以双侧 α 为目标（Rosenberg 2005，式 10 后）。",
    "共用 τ² 截断为零；此时共用 τ² 的随机效应结果与固定效应结果一致。",
    "分析",
    "区间两端分别按同一公式换算（Daly 代入法）；输入的置信水平只作记录，不重新推算；所得 NNT 区间未计入假定 CER 的不确定性。",
    "协变量类别样本稀少或变量接近共线时，系数估计可能不稳定。",
    "单组总人数",
    "原始比值效应在计算前取对数；FISHER_Z 输入已是 Fisher z；其他效应保持自然尺度。",
    "原始表中有零格，已对四格均加 0.5；请与不校正结果比较。",
    "原始表中有零格，未应用连续性校正。",
    "参考标准",
    "双侧渐近 Kendall τ-b 检验，已调整并列秩。",
    "可信",
    "可用",
    "合并分析",
    "合并效应 ML 轮廓似然区间",
    "合并结果",
    "同一结局与时间点下，每项独立研究只取一个效应值；模型未处理研究内协方差。",
    "启用已核验记录",
    "固定效应模型将 τ² 设为 0，因此该预测区间只反映同质性假设下新研究的抽样误差；报告时应使用随机效应预测区间。",
    "固定效应逆方差",
    "固定效应逆方差模型",
    "在研究相互独立的假设下，按 Stouffer 非加权方法合并已观察研究的 Z 值。",
    "多选",
    "失效安全数不能证明发表偏倚，也不能估计实际缺失研究数或解释缺失原因。",
    "对数尺度",
    "对照组总人数",
    "导入来源",
    "小样本研究效应模式也可能来自异质性、偶然因素或研究设计差异。本拟合不能证明发表偏倚或结局报告偏倚，也不能给出因果解释。",
    "已激活",
    "已知抽样方差的 WLS 正态 95% Wald 区间。",
    "当前计算无法得到有限的对数比值；估计值和标准误不可用。",
    "待核验候选组",
    "待评价试验",
    "截距是外推至 SE=0 时的拟合效应；区间使用已知抽样方差计算。",
    "所有效应值和标准误须使用同一分析尺度（比值取对数，相关系数用 Fisher z）；上游零单元格或连续性校正也会改变失效安全数。",
    "所有效应值对应同一比较、结局、时间点和指标；每行是一项独立研究。",
    "所选区间是渐近近似，实际覆盖率可能偏离标称水平，区间通常也不以原始观察值为中心。",
    "批注面板已收起",
    "报告的事件数",
    "按公式估算的均值和 SD 不是观测数据；普通效应量标准误未计入重建不确定性。",
    "排名以拟合得到的共同 τ² 和正态近似为条件，不传播异质性估计的不确定性，并沿用一致性模型的传递性假设。",
    "操作",
    "效应值以逆方差加权均值为中心，按校正后的方差标准化；检验使用渐近零假设近似。",
    "效应量与合并分析",
    "数据保存在本机",
    "文献初筛",
    "方法学参考与引用提示（请查阅原书核对）",
    "暂不确定",
    "最大似然随机效应",
    "来源文件",
    "来源记录",
    "来源：",
    "查看轨迹",
    "标准误与所分析的效应值使用同一尺度，并用于计算抽样方差。",
    "标准误会检查为有限正数，但不参与 Orwin 公式计算。",
    "此区间仅描述单项研究的配对表；不提供标准误，也不能从区间端点反推标准误用于合并。Meta 分析仍应使用已有的 RD 与标准误。",
    "此处仅实现固定效应版本；原文随机效应版本需迭代按 1/(SE²+合并 τ²) 重新加权，且经常退化为固定效应结果（式 11–13）。",
    "此敏感性指标不能估计实际未发表研究数，也不能证明发表偏倚。",
    "此敏感性指标不能证明发表偏倚、估计实际未发表研究数，也不能校正已存在的发表偏倚（Rosenberg 2005，第 467 页）。",
    "每个亚组分别估计 DL τ²；每组研究较少时估计不精确。每组少于约 10–20 项研究时通常宜共用 τ²（Borenstein 2021，第 21 章）。",
    "每行是一项独立研究，按抽样方差的倒数加权：w=1/SE²（Rosenberg 2005，式 3–4）。",
    "每行是一项独立研究，计算已观察效应值的算术平均值时各研究权重相同。",
    "比值估计在对数尺度分析，标准误也须已在该尺度；FISHER_Z 保持 Fisher z 尺度。",
    "比值类效应的标准误必须在对数比值尺度输入；程序不会从原始比值尺度自动换算。",
    "比较完整样本与排除后样本",
    "比较模型与区间方法的敏感性",
    "治疗组总人数",
    "点估计值不在输入的置信区间内，请核对原始数据。",
    "留一法分析",
    "研究少于 10 项时请谨慎解读：回归效能和校准可能有限。10 项只是经验参考，不是有效性门槛。",
    "研究数少于 10：亚组分析效能低，τ² 估计不精确（Cochrane Handbook §10.11.5.1）。",
    "研究数少于 10：检验效能和校准可能不足，请谨慎解读；10 只是经验提醒，不是有效性门槛。",
    "研究数较少时，正态 Wald 区间可能低估不确定性。",
    "研究来源：{0}",
    "研究间方差估计比较",
    "稳健推断只改变标准误、检验和区间；点估计值与 τ² 与模型拟合结果相同。",
    "空模型 τ² 大于零时，解释的异质性 R²*=max(0, 1−τ²模型/τ²空模型)；空模型 τ² 为零时该指标不可计算。",
    "算术平均值未考虑研究精度、异质性或研究间依赖；每项独立研究只取一个效应值。",
    "结局为二分类，且两组采用相同的事件定义。",
    "结局测量",
    "编码维度",
    "缺乏支持",
    "置信区间",
    "置信区间端点超出风险差的可取范围 [−1, 1]，已截断到边界。",
    "自然尺度",
    "至少一个边际比例为 0/n 或 n/n，相应 Wilson 区间退化，故该方法得出零宽区间。",
    "至少一个边际计数为零；按 Newcombe 方法本身的定义，相关系数 ψ 设为 0。",
    "至少一项研究的杠杆值与 1 相差不超过 1e−12；其 CR2 调整按 clubSandwich 约定设为零。",
    "至少需要 10 项研究、满秩设计矩阵和正的残差自由度。",
    "至少需要三项研究。",
    "计算 τ² 的 95% REML 轮廓区间",
    "请先选择任务",
    "请先选择任务。",
    "请明确选择 Hozo、Wan 或 Luo；Luo 均值搭配 Wan SD。请比较纳入与排除估算研究的结果，并引用所用方法。",
    "请选择效应方向与比较组的对应关系。",
    "这里的稳健推断不同于 Knapp–Hartung：KH 按残差异质性调整模型协方差；本工具的三明治估计将每项研究作为独立簇，在方差设定错误时仍可保持一致性。簇稳健文献更推荐小样本使用 CR2 与 Satterthwaite 自由度（Pustejovsky 与 Tipton 2018；Imbens 与 Kolesar 2016）。",
    "零效应值与标准误均在分析尺度上：比值类为对数尺度，相关系数为 Fisher z，其余为自然尺度。",
    "预测区间至少需要三项研究（t 分位数自由度为 k−2）。",
    "风险差区间的一端恰为零，相应 NNT 边界为无穷大，按单侧无界区间报告。",
    "风险差区间跨越零，NNT 区间因此无界，包含获益和伤害两侧；按 Altman（1998）的双分支形式报告。",
    "首页 & 设置",
    "高",
))



def _bracket_match(s, i):
    depth = 0
    j = i
    instr = None
    while j < len(s):
        c = s[j]
        if instr:
            if c == "\\":
                j += 2
                continue
            if c == instr:
                instr = None
        else:
            if c in "\"'`":
                instr = c
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return j
        j += 1
    raise ValueError("括号失配 @%d" % i)


def _comment_ranges(s):
    ranges = []
    i = 0
    instr = None
    while i < len(s):
        c = s[i]
        if instr:
            if c == "\\":
                i += 2
                continue
            if c == instr:
                instr = None
            i += 1
            continue
        if c in "\"'`":
            instr = c
            i += 1
            continue
        if c == "/" and i + 1 < len(s):
            if s[i + 1] == "/":
                j = s.find("\n", i)
                j = len(s) if j < 0 else j
                ranges.append((i, j))
                i = j
                continue
            if s[i + 1] == "*":
                j = s.find("*/", i + 2)
                j = len(s) if j < 0 else j + 2
                ranges.append((i, j))
                i = j
                continue
        i += 1
    return ranges


def _in_ranges(ranges, pos):
    import bisect
    idx = bisect.bisect_right(ranges, (pos, float("inf"))) - 1
    return idx >= 0 and ranges[idx][0] <= pos < ranges[idx][1]


def _extract_array(src, name):
    m = re.search(r"\bconst " + name + r"\s*=\s*\[", src)
    start = src.index("[", m.start())
    return src[start:_bracket_match(src, start) + 1]


_REPLAY_ENGINE = r"""
const input = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const languages = Array.from({ length: 10 }, () => [0, 0]);
const dictionary = new Map();
const registrations = new Map();   // zh -> [{source, en}]
let source = 'seed';
function note(zh, en) {
  if (!registrations.has(zh)) registrations.set(zh, []);
  registrations.get(zh).push({ source, en });
}
/* 与 index.html RFLang.register() 逐字同构（含 previous 回填与 aliases） */
function register(pairs) {
  for (const entry of pairs) {
    const [zh, en] = entry;
    const previous = dictionary.get(zh) || dictionary.get(en);
    const row = languages.map((_, index) => entry[index] || previous?.[index] || en);
    note(zh, row[1]);
    for (const value of row) if (value) dictionary.set(value, row);
    dictionary.set(zh, row); dictionary.set(en, row);
    for (const alias of entry.aliases || []) dictionary.set(alias, row);
  }
}
const errors = [];
const seedRows = [];
for (const s of input.seed_srcs) {
  try { seedRows.push(...Function('return ' + s)()); }
  catch (e) { errors.push('seed: ' + String(e)); }
}
/* 种子语义：rows.push(...APP_ROWS, ...PRISMA_ROWS)；Map zh->row（后行胜） */
const seedMap = new Map();
for (const row0 of seedRows) { note(row0[0], row0[1]); seedMap.set(row0[0], row0); }
for (const [k, v] of seedMap) dictionary.set(k, v);
for (const r of input.regs) {
  source = r.file;
  if (r.kind === 'literal') {
    try { register(Function('return ' + r.src)()); }
    catch (e) { errors.push(r.file + ': ' + String(e)); }
  } else {
    globalThis.window = globalThis;
    globalThis.RFLang = { register };
    try { Function(r.src)(); }
    catch (e) { errors.push(r.file + ' prefix: ' + String(e)); }
  }
}
const conflictKeys = [];
for (const [zh, regs] of registrations) {
  if (regs.length < 2) continue;
  if (new Set(regs.map(r => r.en)).size > 1) conflictKeys.push(zh);
}
const winners = {};
for (const zh of Object.keys(input.named)) winners[zh] = (dictionary.get(zh) || [null, null])[1];
process.stdout.write(JSON.stringify({
  conflictKeys: conflictKeys.sort(), winners, errors,
  named_registrations: Object.fromEntries(
    Object.entries(input.named).map(([zh]) => [zh, registrations.get(zh) || []])),
}));
"""


def _replay_dictionary():
    """按 index.html 真实加载顺序回放全部词典注册，返回回放结果 dict。"""
    html = INDEX.read_text(encoding="utf-8")
    seed_srcs = [_extract_array(html, n) for n in ("rows", "APP_ROWS", "PRISMA_ROWS")]
    scripts = re.findall(r'<script src="\./([^"?]+)\?v=', html)
    regs = []
    for fname in scripts:
        p = FRONTEND / fname
        if not p.is_file():
            continue
        js = p.read_text(encoding="utf-8", errors="replace")
        cranges = _comment_ranges(js)
        for m in re.finditer(r"RFLang\??\.register\s*\(", js):
            if _in_ranges(cranges, m.start()):
                continue  # 注释里的提及不是调用
            k = m.end()
            while js[k] in " \t\r\n":
                k += 1
            if js[k] == "[":
                regs.append({"file": fname, "kind": "literal", "src": js[k:_bracket_match(js, k) + 1]})
            else:
                end = js.index(")", k)
                regs.append({"file": fname, "kind": "prefix", "src": js[:end + 1] + "\n})();"})
    payload = json.dumps({
        "seed_srcs": seed_srcs, "regs": regs,
        "named": {k: 1 for k in EXPECTED_WINNERS},
    })
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        handle.write(payload)
        path = handle.name
    exe = _node_exe()
    if exe is None:
        raise unittest.SkipTest("本机无 node 可执行文件（词典回放不可用）")
    try:
        proc = subprocess.run([exe, "-e", _REPLAY_ENGINE.replace("process.argv[2]", "process.argv[1]"), path],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError("词典回放失败：%s" % proc.stderr[-1500:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


class DictionaryConceptConflictTests(unittest.TestCase):
    """MM5-03 钉子：英文互异的概念级冲突键必须为 0；措辞类白名单计数并保鲜。"""

    def test_no_concept_level_english_conflicts(self):
        result = _replay_dictionary()
        self.assertEqual(result["errors"], [], "词典回放本身不得出错")
        conflicts = set(result["conflictKeys"])

        concept_leak = sorted(conflicts & CONCEPT_KEYS)
        self.assertEqual(
            concept_leak, [],
            "MM5-03：概念级冲突键未清零（英文互异 → 全语言显示错词）：%r" % concept_leak,
        )
        unlisted = sorted(conflicts - WORDING_ALLOWLIST - CONCEPT_KEYS)
        self.assertEqual(
            unlisted, [],
            "MM5-03：出现未登记的新概念级冲突键（要么修复，要么按措辞类登记白名单）：%r" % unlisted,
        )
        stale = sorted(WORDING_ALLOWLIST - conflicts)
        self.assertEqual(
            stale, [],
            "白名单条目已不再冲突（措辞类已收敛），应从白名单移除（保持计数真实）：%r" % stale,
        )
        # 措辞类冲突计数入报告（r1 评审修正：原 assertGreaterEqual(x, 0) 恒真）：
        # 同一概念的不同英文措辞/大小写/单复数，胜者=script 注册顺序，允许保留但
        # 数量必须与钉子登记值一致——收敛了就要下调登记值并从白名单移除条目。
        wording_count = len(conflicts & WORDING_ALLOWLIST)
        report = ("[MM5-03] 词典回放报告：英文互异冲突键 %d = 概念级 %d（必须 0）"
                  "+ 措辞类 %d（白名单登记 %d 条，逐条仍在冲突）"
                  % (len(conflicts), len(concept_leak), wording_count, len(WORDING_ALLOWLIST)))
        print(report)
        self.assertEqual(
            wording_count, EXPECTED_WORDING_CONFLICT_COUNT,
            "措辞类冲突计数 %d 偏离钉子登记值 %d：%s（修复措辞冲突后请同步下调"
            " EXPECTED_WORDING_CONFLICT_COUNT 并从 WORDING_ALLOWLIST 移除对应条目）"
            % (wording_count, EXPECTED_WORDING_CONFLICT_COUNT, report),
        )

    def test_named_flagship_keys_render_expected_english(self):
        result = _replay_dictionary()
        self.assertEqual(result["errors"], [])
        for zh, expected in sorted(EXPECTED_WINNERS.items()):
            self.assertEqual(
                result["winners"].get(zh), expected,
                "键 %r 的胜者英文应为 %r（注册记录：%r）"
                % (zh, expected, result["named_registrations"].get(zh)),
            )


class RegisterConflictWarnTests(unittest.TestCase):
    """MM5-03 钉子：register() 对同 zh 键不同行的重复注册 console.warn（行为不变）。"""

    def test_register_warns_on_conflicting_reregistration(self):
        html = INDEX.read_text(encoding="utf-8")
        m = re.search(r"register\(pairs\) \{.*?\n    \},", html, re.S)
        self.assertTrue(m, "index.html 中未找到 RFLang.register 方法体")
        register_body = m.group(0)
        register_body = register_body[:register_body.rindex(",")]  # 去掉方法尾逗号
        langs = re.search(r"\n  const languages = \[.*?\];", html, re.S)
        self.assertTrue(langs, "index.html 中未找到 languages 数组")
        script = r"""
'use strict';
const languages = __LANGS__;
const dictionary = new Map();
let reverseLanguage = -1;
const observer = { disconnect() {} };
const document = { body: {} };
function translateTree() {}
function observe() {}
const warnings = [];
const console = { warn: (...a) => warnings.push(a.map(String).join(' ')) };
function register(pairs) __REGISTER__
register([['页码', 'Page number', 'x1'], ['说明', 'Note', 'y1']]);
register([['页码', 'Pages', 'x2']]);      // 冲突：同 zh 键不同行 → 必须告警
register([['说明', 'Note', 'y1']]);        // 内容完全一致 → 不得告警
register([['新键', 'Fresh', 'f1']]);       // 全新键 → 不得告警
process.stdout.write(JSON.stringify({ warnings,
  renders: [dictionary.get('页码')[1], dictionary.get('说明')[1]] }));
""".replace("__LANGS__", langs.group(0).split("=", 1)[1].rstrip(";").strip()) \
     .replace("__REGISTER__", register_body[register_body.index("{"):])
        out = json.loads(_run_node(script).strip().splitlines()[-1])
        self.assertEqual(len(out["warnings"]), 1,
                         "同 zh 键不同行应恰好告警一次（重复注册相同内容不告警）：%r" % out["warnings"])
        self.assertIn("页码", out["warnings"][0])
        self.assertIn("Page number", out["warnings"][0])
        self.assertIn("Pages", out["warnings"][0])
        # 行为不变：覆盖语义保持（后注册者胜）
        self.assertEqual(out["renders"], ["Pages", "Note"])


# =====================================================================
# MM5-17：注释 9 语言 → 10 语言
# =====================================================================

class LanguageCountCommentTests(unittest.TestCase):

    def test_multilingual_engine_comment_counts_ten_languages(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("同一套 10 语言字典", html)
        self.assertNotIn("同一套 9 语言字典", html)
        self.assertIn("塞尔维亚语", html)


if __name__ == "__main__":
    unittest.main()
