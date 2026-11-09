"""筛选训练（Pilot Screening）纯函数与任务协作库存储（2.1.5 G2/W2）。

权威设计：``out/release_plan/drafts/design_2.1.5_pilot_screening.md`` v1.0。
本模块只做两件事：

1. **计算**（§五）：``evaluate`` 按设计输出 κ/PABAK/一致率/三档结论/逐条对照。
   二分类 κ 与三分类原始 κ 一律复用 ``coscreen.coding_agreement`` 既有实现
   （``summarize_agreement``），本模块不重写 κ 公式。
2. **存储**（§四）：``pilot_*`` 七张表全部落在任务的**协作库**
   （``collaboration.db``）内，``CREATE TABLE IF NOT EXISTS`` 可加幂等——
   旧版程序不读即忽略，且与正式筛选库（decisions/stage2_decisions/编码/
   轨迹）物理隔离（语义 S5 零污染）。

语义要点（S1–S12）：
- S1 可选：``pilot_enabled`` 默认关（默认值只在读时给出，不落盘）；
- S2 非强制：``enforce_pass`` 默认 false，只在正式初筛写入口做结构化 403；
- S3/S4 盲纪律：作答期间零金标准；交卷后才揭示逐条对照；
- S6/S7 三档结论与"新批重训"：每次尝试独立存档，publish 后金标准只读；
- S8 阈值随结果快照存档（报告口径可追溯，绝不事后重算）；
- S9/S10 maybe 只在训练里存在；任一方 maybe 或分歧 → borderline 边界案例。
- 只认首次成绩（终审 #19 用户裁定）：交卷后同集仍可新开尝试（练习价值），
  但 enforce 准入（``cleared``）、负责人看板结论计数、结果视图
  ``my_result`` 与其方法学段、「讨论后进入」只认每套题的**首次** submitted
  尝试；后续尝试带 ``counts_for_clearance=False`` / ``practice=True`` 标记。

SQL 纪律：全部语句为固定字面量 + ``?`` 参数绑定，无 f-string/format/拼接。
"""

from __future__ import annotations

import json
import random
import secrets
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from coscreen import coding_agreement
from coscreen import task_collaboration

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DECISIONS",
    "PilotScreeningError",
    "SAMPLE_LIMITS",
    "SAMPLINGS",
    "SET_STATUSES",
    "classify_verdict",
    "cleared",
    "connect",
    "create_set",
    "current_attempt",
    "enforce_blocked",
    "ensure_schema",
    "evaluate",
    "get_set",
    "get_settings",
    "has_schema",
    "list_sets",
    "member_history",
    "member_state",
    "methodology_text",
    "normalize_thresholds",
    "results_board",
    "sample_keys",
    "set_settings",
    "store_path",
]

#: 训练集与金标准/作答的枚举（正式两阶段只允许 include/exclude，maybe 是
#: 训练专属的三选项——设计 S9 / 二分类方案 §一）。
DECISIONS = ("include", "exclude", "maybe")
SET_STATUSES = ("draft", "published", "archived")
ATTEMPT_STATUSES = ("in_progress", "submitted")
SAMPLINGS = ("random", "curated", "mixed")
VERDICTS = ("pass", "borderline", "fail")

#: S8 默认阈值（JBI 百分比口径）。建集时可改，随每次结果快照存档。
DEFAULT_THRESHOLDS = {"min_kappa": 0.61, "target_kappa": 0.70,
                      "agreement_floor": 0.75}

#: S11 抽样范围。
SAMPLE_LIMITS = (10, 100)

_DEFAULT_SIZE = 50
_MAX_REASON = 2000
_MAX_NOTE = 4000

#: 进程内写串行化（跨进程仍由 ``BEGIN IMMEDIATE`` 兜底）。
_WRITE_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_sets (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  created_by      INTEGER NOT NULL CHECK(created_by > 0),
  size            INTEGER NOT NULL CHECK(size BETWEEN 1 AND 1000),
  sampling        TEXT NOT NULL CHECK(sampling IN ('random', 'curated', 'mixed')),
  seed            INTEGER,
  thresholds_json TEXT NOT NULL,
  note            TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'draft'
                  CHECK(status IN ('draft', 'published', 'archived')),
  created_at      TEXT NOT NULL,
  published_at    TEXT NOT NULL DEFAULT '',
  archived_at     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pilot_items (
  set_id     INTEGER NOT NULL REFERENCES pilot_sets(id) ON DELETE CASCADE,
  zotero_key TEXT NOT NULL,
  position   INTEGER NOT NULL,
  PRIMARY KEY(set_id, zotero_key)
);
CREATE TABLE IF NOT EXISTS pilot_gold (
  set_id     INTEGER NOT NULL,
  zotero_key TEXT NOT NULL,
  decision   TEXT NOT NULL CHECK(decision IN ('include', 'exclude', 'maybe')),
  reason     TEXT NOT NULL DEFAULT '',
  decided_by INTEGER NOT NULL,
  decided_at TEXT NOT NULL,
  PRIMARY KEY(set_id, zotero_key),
  FOREIGN KEY(set_id, zotero_key)
    REFERENCES pilot_items(set_id, zotero_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS pilot_attempts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  set_id           INTEGER NOT NULL REFERENCES pilot_sets(id) ON DELETE CASCADE,
  trainee_user_id  INTEGER NOT NULL CHECK(trainee_user_id > 0),
  status           TEXT NOT NULL DEFAULT 'in_progress'
                   CHECK(status IN ('in_progress', 'submitted')),
  started_at       TEXT NOT NULL,
  submitted_at     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pilot_attempts_member
  ON pilot_attempts(set_id, trainee_user_id);
CREATE TABLE IF NOT EXISTS pilot_decisions (
  attempt_id  INTEGER NOT NULL REFERENCES pilot_attempts(id) ON DELETE CASCADE,
  zotero_key  TEXT NOT NULL,
  decision    TEXT NOT NULL CHECK(decision IN ('include', 'exclude', 'maybe')),
  reason      TEXT NOT NULL DEFAULT '',
  answered_at TEXT NOT NULL,
  PRIMARY KEY(attempt_id, zotero_key)
);
CREATE TABLE IF NOT EXISTS pilot_results (
  attempt_id            INTEGER PRIMARY KEY
                        REFERENCES pilot_attempts(id) ON DELETE CASCADE,
  snapshot_json         TEXT NOT NULL,
  verdict               TEXT NOT NULL
                        CHECK(verdict IN ('pass', 'borderline', 'fail')),
  entered_by_discussion INTEGER NOT NULL DEFAULT 0
                        CHECK(entered_by_discussion IN (0, 1)),
  discussion_note       TEXT NOT NULL DEFAULT '',
  discussed_at          TEXT NOT NULL DEFAULT '',
  decided_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pilot_task_settings (
  singleton    INTEGER PRIMARY KEY CHECK(singleton = 1),
  pilot_enabled INTEGER NOT NULL DEFAULT 0 CHECK(pilot_enabled IN (0, 1)),
  enforce_pass  INTEGER NOT NULL DEFAULT 0 CHECK(enforce_pass IN (0, 1)),
  updated_by    INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT NOT NULL DEFAULT ''
);
"""


class PilotScreeningError(ValueError):
    """业务错误：``code`` 由 API 层映射为结构化 4xx（message_key=pilot.<code>）。

    ``params`` 既可直接传字典（``params={"missing": 1}``），也可用关键字
    （``field="min_kappa"``）；两种写法合并进同一载荷。
    """

    def __init__(self, code: str, message: str = "",
                 params: Mapping[str, Any] | None = None, **extra: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.params: dict[str, Any] = dict(params or {})
        self.params.update(extra)


# ---------------------------------------------------------------------------
# 存储基础
# ---------------------------------------------------------------------------

def store_path(task_dir: str | Path) -> Path:
    """训练数据落点：任务协作库（``collaboration.db``，设计 §四"任务协作库"）。"""
    return task_collaboration.shared_store_path(task_dir)


def connect(path: str | Path, *, readonly: bool = False,
            timeout: float = 15.0) -> sqlite3.Connection:
    """打开库；``readonly`` 用 ``mode=ro``（不建库、不跑 DDL）。"""
    path = Path(path)
    if readonly:
        if not path.is_file():
            raise FileNotFoundError(path)
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout,
                               isolation_level=None)
    else:
        conn = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """可加幂等建表（旧库缺表补齐；旧版程序不读这些表即忽略）。"""
    conn.executescript(_SCHEMA)


def has_schema(conn: sqlite3.Connection) -> bool:
    """只读探针：协作库是否已有 pilot 表（legacy 库返回 False，零 DDL）。"""
    try:
        conn.execute("SELECT 1 FROM pilot_task_settings LIMIT 1").fetchone()
        return True
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return False
        raise


@contextmanager
def write(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """写事务（跨进程 BEGIN IMMEDIATE + 进程内锁）。"""
    with _WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def _new_seed() -> int:
    """未被指定时生成一个登记用的抽样 seed（系统熵，仅此一次；之后恒用登记值）。"""
    return secrets.randbelow(1 << 31)


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# 阈值与抽样（纯函数）
# ---------------------------------------------------------------------------

def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PilotScreeningError("threshold_invalid", params={"field": name})
    try:
        number = float(value)
    except OverflowError as exc:
        # 复审 r2（终审 #20 收窄）：10**400 这类合法 int 在 float() 转换时抛
        # OverflowError——按契约（threshold_invalid：越界/非数值 → 400）折叠，
        # 不再上抛被 _run 误当 set_id 越界折叠成 404。
        raise PilotScreeningError(
            "threshold_invalid", params={"field": name}) from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise PilotScreeningError("threshold_invalid", params={"field": name})
    return number


def normalize_thresholds(thresholds: Mapping[str, Any] | None) -> dict[str, float]:
    """校验并补全阈值（S8）；非法值一律结构化 ``threshold_invalid``。"""
    if thresholds is None:
        return dict(DEFAULT_THRESHOLDS)
    if not isinstance(thresholds, Mapping):
        raise PilotScreeningError("threshold_invalid", params={"field": "thresholds"})
    merged = dict(DEFAULT_THRESHOLDS)
    for key in DEFAULT_THRESHOLDS:
        if key in thresholds:
            merged[key] = thresholds[key]
    for key in DEFAULT_THRESHOLDS:
        merged[key] = _number(merged[key], key)
    for key in ("min_kappa", "target_kappa"):
        if not -1.0 <= merged[key] <= 1.0:
            raise PilotScreeningError("threshold_invalid", params={"field": key})
    if not 0.0 <= merged["agreement_floor"] <= 1.0:
        raise PilotScreeningError("threshold_invalid",
                                  params={"field": "agreement_floor"})
    if merged["target_kappa"] < merged["min_kappa"]:
        raise PilotScreeningError("threshold_invalid",
                                  params={"field": "target_kappa"})
    return merged


def _size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PilotScreeningError("size_out_of_range",
                                  params={"min": SAMPLE_LIMITS[0],
                                          "max": SAMPLE_LIMITS[1]})
    if not SAMPLE_LIMITS[0] <= value <= SAMPLE_LIMITS[1]:
        raise PilotScreeningError("size_out_of_range",
                                  params={"min": SAMPLE_LIMITS[0],
                                          "max": SAMPLE_LIMITS[1]})
    return value


def sample_keys(available: Sequence[str], *, size: int | None,
                sampling: str, seed: int | None = None,
                curated_keys: Sequence[str] | None = None) -> list[str]:
    """按设计 S11 抽样：random（seed 可复现）/ curated / mixed。

    - random：``random.Random(seed).sample(sorted(pool), k)``——结果与入参
      顺序无关，同 seed 恒等（可复现钉）；
    - curated：导师手选顺序原样保留，必须全部存在于文献目录；
    - mixed：手选在前，其余按 seed 从剩余池补齐。

    随机源说明：此处是**方法学可复现抽样**（S11 要求"带 seed 可复现"、
    登记 seed 进报告段），不是加密/安全用途——按设计必须使用可复现的种子
    随机数（同一 seed 恒得同一抽样），因此 `random.Random(seed)` 是正确
    选择；只有 seed 本身由 `_new_seed()` 用系统熵一次性生成后落库。
    """
    if sampling not in SAMPLINGS:
        raise PilotScreeningError("sampling_invalid", params={"sampling": sampling})
    pool = sorted({str(key) for key in available})
    curated: list[str] = []
    if curated_keys is not None:
        if isinstance(curated_keys, (str, bytes)) or not isinstance(
                curated_keys, Sequence):
            raise PilotScreeningError("curated_key_unknown", params={"key": ""})
        for key in curated_keys:
            text = str(key)
            if text not in pool:
                raise PilotScreeningError("curated_key_unknown", params={"key": text})
            if text not in curated:
                curated.append(text)
    if sampling == "curated":
        if not curated:
            raise PilotScreeningError("curated_key_unknown", params={"key": ""})
        if size is not None and size != len(curated):
            raise PilotScreeningError(
                "size_mismatch", params={"size": size, "curated": len(curated)})
        resolved = len(curated)
        _size(resolved)
        return curated
    if size is None:
        size = _DEFAULT_SIZE
    size = _size(size)
    if sampling == "mixed":
        if len(curated) > size:
            raise PilotScreeningError(
                "size_mismatch", params={"size": size, "curated": len(curated)})
        remaining = [key for key in pool if key not in set(curated)]
        need = size - len(curated)
        if len(remaining) < need:
            raise PilotScreeningError("insufficient_articles",
                                      params={"available": len(pool)})
        rng = random.Random(seed)
        return curated + rng.sample(remaining, need)
    if len(pool) < size:
        raise PilotScreeningError("insufficient_articles",
                                  params={"available": len(pool)})
    rng = random.Random(seed)
    return rng.sample(pool, size)


# ---------------------------------------------------------------------------
# 计算（§五）：κ 复用 coding_agreement，不重写
# ---------------------------------------------------------------------------

def _pair(value: Any) -> tuple[str | None, str]:
    if value is None:
        return None, ""
    if isinstance(value, Mapping):
        decision = value.get("decision")
        reason = value.get("reason")
        return (None if decision is None else str(decision),
                "" if reason is None else str(reason))
    return str(value), ""


def _binary(decision: str) -> str:
    """二分类主口径：maybe → 非 include（保守，S9）。"""
    return "include" if decision == "include" else "exclude"


def _kappa(records: list[dict]) -> tuple[float | None, float | None, str]:
    """调用既有 ``summarize_agreement``，返回 (κ, 一致率, 状态)。

    退化边际（双方所有条目同一类别，pe=1 → 0/0）按惯例折叠：完美一致记
    κ=1.0，否则 0.0；状态原样保留供报告披露（不静默隐藏口径）。
    """
    summary = coding_agreement.summarize_agreement(
        records, ("gold", "trainee"), "categorical")
    kappa = summary.get("cohen_kappa")
    status = str(summary.get("kappa_status") or "")
    rate = summary.get("agreement_rate")
    if kappa is None and status == "degenerate_marginals":
        kappa = 1.0 if rate == 1.0 else 0.0
    return kappa, rate, status


def classify_verdict(kappa: float | None, agreement: float | None,
                     thresholds: Mapping[str, Any]) -> str:
    """S7 三档：达标（κ≥target 且一致率≥floor）/ 临界（κ≥min）/ 未达标。"""
    limits = normalize_thresholds(thresholds)
    if kappa is None or agreement is None:
        return "fail"
    if kappa >= limits["target_kappa"] and agreement >= limits["agreement_floor"]:
        return "pass"
    if kappa >= limits["min_kappa"]:
        return "borderline"
    return "fail"


def evaluate(gold: Mapping[str, Any], trainee: Mapping[str, Any],
             thresholds: Mapping[str, Any] | None = None, *,
             order: Sequence[str] | None = None, sampling: str = "") -> dict:
    """按设计 §五 计算一致性结论（纯函数，零 IO）。

    ``gold`` / ``trainee``：``zotero_key -> decision`` 或
    ``zotero_key -> {"decision", "reason"}``。返回逐条对照 + 指标 + 结论；
    结果直接进快照存档（S8），报告段只读快照。
    """
    if not isinstance(gold, Mapping) or not isinstance(trainee, Mapping):
        raise PilotScreeningError("invalid_input")
    limits = normalize_thresholds(thresholds)
    keys = list(order) if order is not None else sorted(gold)
    items: list[dict] = []
    for key in keys:
        gold_decision, gold_reason = _pair(gold.get(key))
        trainee_decision, trainee_reason = _pair(trainee.get(key))
        if gold_decision is not None and gold_decision not in DECISIONS:
            raise PilotScreeningError("invalid_decision",
                                      params={"decision": gold_decision})
        if trainee_decision is not None and trainee_decision not in DECISIONS:
            raise PilotScreeningError("invalid_decision",
                                      params={"decision": trainee_decision})
        disagreement = (gold_decision is not None and trainee_decision is not None
                        and gold_decision != trainee_decision)
        borderline = (gold_decision == "maybe" or trainee_decision == "maybe"
                      or disagreement)
        items.append({
            "zotero_key": key,
            "gold_decision": gold_decision,
            "gold_reason": gold_reason,
            "trainee_decision": trainee_decision,
            "trainee_reason": trainee_reason,
            "disagreement": disagreement,
            "borderline": borderline,
        })
    paired = [row for row in items
              if row["gold_decision"] is not None
              and row["trainee_decision"] is not None]
    binary_records: list[dict] = []
    raw_records: list[dict] = []
    for row in paired:
        binary_records.append({"item_key": row["zotero_key"], "rater_id": "gold",
                               "value": _binary(row["gold_decision"]),
                               "field_key": "decision"})
        binary_records.append({"item_key": row["zotero_key"], "rater_id": "trainee",
                               "value": _binary(row["trainee_decision"]),
                               "field_key": "decision"})
        raw_records.append({"item_key": row["zotero_key"], "rater_id": "gold",
                            "value": row["gold_decision"], "field_key": "decision"})
        raw_records.append({"item_key": row["zotero_key"], "rater_id": "trainee",
                            "value": row["trainee_decision"],
                            "field_key": "decision"})
    kappa_binary, agreement_binary, binary_status = _kappa(binary_records)
    kappa_raw, agreement, raw_status = _kappa(raw_records)
    if agreement is None:
        agreement = 0.0  # 无配对条目（理论上不会发生：交卷要求全答）
    pabak = (2 * agreement_binary - 1) if agreement_binary is not None else None
    metrics = {
        "agreement": agreement,
        "agreement_binary": agreement_binary,
        "kappa_binary": kappa_binary,
        "kappa_raw": kappa_raw,
        "pabak": pabak,
        "maybe_gold_count": sum(1 for row in paired
                                if row["gold_decision"] == "maybe"),
        "maybe_trainee_count": sum(1 for row in paired
                                   if row["trainee_decision"] == "maybe"),
        "kappa_binary_status": binary_status,
        "kappa_raw_status": raw_status,
        "n_paired": len(paired),
    }
    verdict = classify_verdict(kappa_binary, agreement, limits)
    return {
        "schema_version": 1,
        "computed_at": _now(),
        "sampling": sampling,
        "n_items": len(items),
        "thresholds": limits,
        "metrics": metrics,
        "verdict": verdict,
        "items": items,
    }


#: 抽样方式的中英标签（只进报告段文本，不是界面词典术语）。
_SAMPLING_LABELS = {
    "random": ("随机", "random"),
    "curated": ("导师手选边界案例", "curated borderline cases"),
    "mixed": ("混合（手选+随机）", "mixed (curated + random)"),
}

_VERDICT_LABELS = {
    "pass": ("达标", "met the calibration target"),
    "borderline": ("临界", "borderline (discussion required before formal screening)"),
    "fail": ("未达标", "below the calibration floor"),
}


def methodology_text(snapshot: Mapping[str, Any], *, lang: str = "zh") -> str:
    """设计 §七 方法学报告段：全部数字取自快照，不重算（S8）。"""
    metrics = snapshot.get("metrics") or {}
    limits = snapshot.get("thresholds") or DEFAULT_THRESHOLDS
    sampling = str(snapshot.get("sampling") or "")
    n_items = snapshot.get("n_items", 0)
    verdict = str(snapshot.get("verdict") or "")
    entered = bool(snapshot.get("entered_by_discussion"))

    def fmt_threshold(value: Any) -> str:
        # 阈值按配置原精度展示（至少两位小数）：默认 0.61/0.70/0.75 两位，
        # 建集自定义的 0.614 三位、0.6145 四位——恒 .2f 会把 0.614 显示成
        # 0.61，令 κ=0.610 与底线「视觉同值」再现伪矛盾（终审 #51 第 3 轮）。
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        head, _, tail = repr(number).partition(".")
        if not tail or "e" in tail or "E" in tail:  # 整数/科学计数 → 两位
            return f"{number:.2f}"
        if len(tail) < 2:
            tail = tail.ljust(2, "0")
        return f"{head}.{tail}"

    def fmt_metric(value: Any) -> str:
        # 计算指标保留 3 位小数：两位会把 κ∈[0.605,0.61) 展示成与底线同值
        # 的 0.61，与「未达标」结论自相矛盾（终审 #51）。判定仍用原始值。
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "—"

    def _below(value: Any, limit: Any) -> bool:
        try:
            return float(value) < float(limit)
        except (TypeError, ValueError):
            return False

    # 取整伪矛盾（终审 #51）：指标 3 位展示值不低于阈值展示值、而原始值
    # 低于阈值时（同值：κ 0.610 vs 0.61、0.700 vs 0.70；反超：4 位阈值
    # 0.6098 下 κ=0.6098 附近展示 0.610），附未取整原始值消除视觉矛盾；
    # 判定链始终用原始值，不受展示影响。
    kappa_limit = (limits["min_kappa"] if verdict == "fail"
                   else limits["target_kappa"])
    kappa_below = (verdict in ("fail", "borderline")
                   and _below(metrics.get("kappa_binary"), kappa_limit))
    agreement_below = (verdict != "pass"
                       and _below(metrics.get("agreement"),
                                  limits["agreement_floor"]))

    def metric_display(value: Any, limit: Any, below: bool, *,
                       note: str) -> str:
        shown = fmt_metric(value)
        try:
            # 取整伪矛盾按「展示值不低于阈值展示值」判定（below 已蕴含
            # 原始值更低）：0.610 == 0.61、0.700 == 0.70（同值），以及
            # 4 位阈值下的 0.610 > 0.6098（3 位舍入后反超）都附原始值；
            # 0.610 < 0.614、0.538 < 0.6098 视觉可辨则不附。
            if below and float(shown) >= float(fmt_threshold(limit)):
                return shown + note.format(repr(float(value)))
        except (TypeError, ValueError):
            pass
        return shown

    if lang == "en":
        label = _SAMPLING_LABELS.get(sampling, (sampling, sampling))[1]
        verdict_label = _VERDICT_LABELS.get(verdict, (verdict, verdict))[1]
        discussion = ("a discussion entry was recorded before entering formal "
                      "screening" if entered else "no discussion entry was recorded")
        return (
            f"Calibration: before formal screening, {n_items} records sampled by "
            f"{label} were independently screened blind by all screeners and "
            f"compared with the pre-registered gold standard: binary kappa="
            f"{metric_display(metrics.get('kappa_binary'), kappa_limit, kappa_below, note=' (unrounded {0})')} "
            f"(floor {fmt_threshold(limits['min_kappa'])}, "
            f"target {fmt_threshold(limits['target_kappa'])}), agreement="
            f"{metric_display(metrics.get('agreement'), limits['agreement_floor'], agreement_below, note=' (unrounded {0})')} "
            f"(floor {fmt_threshold(limits['agreement_floor'])}), "
            f"PABAK={fmt_metric(metrics.get('pabak'))}. Verdict: {verdict_label}; "
            f"{discussion}. Formal screening {'did' if entered or verdict == 'pass' else 'did not'} "
            "use the revised criteria."
        )
    label = _SAMPLING_LABELS.get(sampling, (sampling, sampling))[0]
    verdict_label = _VERDICT_LABELS.get(verdict, (verdict, verdict))[0]
    discussion = ("已留痕讨论后进入正式筛选" if entered
                  else "未记录讨论后进入")
    return (
        f"校准：正式筛选前，{n_items} 条按{label}抽取的题录（含边界案例）由全部筛员"
        f"独立盲筛，与预登记金标准比较：二分类 κ="
        f"{metric_display(metrics.get('kappa_binary'), kappa_limit, kappa_below, note='（原始值 {0}）')}"
        f"（底线 {fmt_threshold(limits['min_kappa'])}，目标 {fmt_threshold(limits['target_kappa'])}），"
        f"一致率="
        f"{metric_display(metrics.get('agreement'), limits['agreement_floor'], agreement_below, note='（原始值 {0}）')}"
        f"（底线 {fmt_threshold(limits['agreement_floor'])}），"
        f"PABAK={fmt_metric(metrics.get('pabak'))}。结论：{verdict_label}；"
        f"{discussion}。正式筛选"
        f"{'采用' if entered or verdict == 'pass' else '未采用'}修订后标准。"
    )


# ---------------------------------------------------------------------------
# 设置（S1/S2）
# ---------------------------------------------------------------------------

def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """任务级开关：默认关/默认不拦（读不到行时只给默认值，不落盘）。"""
    try:
        row = conn.execute(
            "SELECT pilot_enabled, enforce_pass, updated_at"
            " FROM pilot_task_settings WHERE singleton = 1").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {"pilot_enabled": False, "enforce_pass": False, "updated_at": ""}
        raise
    if row is None:
        return {"pilot_enabled": False, "enforce_pass": False, "updated_at": ""}
    return {"pilot_enabled": bool(row["pilot_enabled"]),
            "enforce_pass": bool(row["enforce_pass"]),
            "updated_at": row["updated_at"] or ""}


def set_settings(conn: sqlite3.Connection, *, user_id: int,
                 pilot_enabled: bool | None = None,
                 enforce_pass: bool | None = None) -> dict[str, Any]:
    """PUT 开关；``enforce_pass=true`` 必须同时 ``pilot_enabled=true``。

    关闭 pilot 自动清掉 enforce（不留悬空拦截闸，S1 零开销）。
    """
    current = get_settings(conn)
    enabled = current["pilot_enabled"] if pilot_enabled is None else bool(pilot_enabled)
    if enforce_pass is None:
        # 未显式给 enforce：关闭 pilot 时顺手清掉（不留悬空拦截闸），
        # 开启 pilot 时保持既有值。
        enforce = current["enforce_pass"] if enabled else False
    else:
        enforce = bool(enforce_pass)
    if enforce and not enabled:
        raise PilotScreeningError("pilot_disabled")
    if not enabled:
        enforce = False
    now = _now()
    with write(conn):
        conn.execute(
            "INSERT INTO pilot_task_settings"
            "(singleton, pilot_enabled, enforce_pass, updated_by, updated_at)"
            " VALUES(1, ?, ?, ?, ?)"
            " ON CONFLICT(singleton) DO UPDATE SET"
            " pilot_enabled=excluded.pilot_enabled,"
            " enforce_pass=excluded.enforce_pass,"
            " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (1 if enabled else 0, 1 if enforce else 0, int(user_id), now))
    return {"pilot_enabled": enabled, "enforce_pass": enforce}


# ---------------------------------------------------------------------------
# 训练集 / 金标准
# ---------------------------------------------------------------------------

def _set_row(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM pilot_sets WHERE id = ?",
                       (int(set_id),)).fetchone()
    if row is None:
        raise PilotScreeningError("set_not_found")
    data = dict(row)
    data["thresholds"] = json.loads(data.pop("thresholds_json") or "{}")
    return data


def _item_keys(conn: sqlite3.Connection, set_id: int) -> list[str]:
    return [str(row[0]) for row in conn.execute(
        "SELECT zotero_key FROM pilot_items WHERE set_id = ? ORDER BY position, zotero_key",
        (int(set_id),))]


def _gold_map(conn: sqlite3.Connection, set_id: int) -> dict[str, dict[str, Any]]:
    return {str(row["zotero_key"]): {"decision": row["decision"],
                                     "reason": row["reason"] or ""}
            for row in conn.execute(
                "SELECT zotero_key, decision, reason FROM pilot_gold"
                " WHERE set_id = ? ORDER BY zotero_key", (int(set_id),))}


def create_set(conn: sqlite3.Connection, *, actor_id: int,
               available_keys: Sequence[str], size: int | None, sampling: str,
               seed: int | None = None, curated_keys: Sequence[str] | None = None,
               thresholds: Mapping[str, Any] | None = None,
               note: str = "") -> dict[str, Any]:
    """建集（设计 §六）：按 S11 抽样并把条目清单固化为 pilot_items。

    random/mixed 未给 seed 时**由后端生成并登记**（响应与详情可见、方法学段
    可引用），保证「同 seed 可复现」不是空头承诺；curated 不需要 seed。
    """
    limits = normalize_thresholds(thresholds)
    if isinstance(seed, bool) or (seed is not None and not isinstance(seed, int)):
        raise PilotScreeningError("seed_invalid")
    if seed is not None and not 0 <= seed < (1 << 31):
        # 终审 #20：pydantic 不给 int 设 64 位上限——19~20 位数字 seed 此前
        # 在 sqlite 绑定时抛 OverflowError（非 DatabaseError）→ 全局 500。
        # 收敛到与 _new_seed 相同的自生成域 [0, 2^31)，越界 400 seed_invalid
        # （params.limit 说明上界，前端可提示）。
        raise PilotScreeningError("seed_invalid", params={"limit": (1 << 31) - 1})
    if seed is None and sampling in ("random", "mixed"):
        seed = _new_seed()
    keys = sample_keys(available_keys, size=size, sampling=sampling, seed=seed,
                       curated_keys=curated_keys)
    if sampling == "curated":
        seed = None
    text = str(note or "")[:_MAX_NOTE]
    now = _now()
    with write(conn):
        cursor = conn.execute(
            "INSERT INTO pilot_sets"
            "(created_by, size, sampling, seed, thresholds_json, note, status, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, 'draft', ?)",
            (int(actor_id), len(keys), sampling, seed,
             json.dumps(limits, ensure_ascii=False, sort_keys=True), text, now))
        set_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO pilot_items(set_id, zotero_key, position) VALUES(?, ?, ?)",
            [(set_id, key, index) for index, key in enumerate(keys)])
    return get_set(conn, set_id)


def list_sets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.*,"
        " (SELECT COUNT(*) FROM pilot_items i WHERE i.set_id = s.id) AS item_count,"
        " (SELECT COUNT(*) FROM pilot_gold g WHERE g.set_id = s.id) AS gold_count,"
        " (SELECT COUNT(*) FROM pilot_attempts a WHERE a.set_id = s.id) AS attempt_count"
        " FROM pilot_sets s ORDER BY s.id DESC").fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["thresholds"] = json.loads(data.pop("thresholds_json") or "{}")
        data["gold_complete"] = data["item_count"] > 0 and (
            data["gold_count"] == data["item_count"])
        result.append(data)
    return result


def get_set(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    data = _set_row(conn, set_id)
    keys = _item_keys(conn, set_id)
    gold = _gold_map(conn, set_id)
    data["item_count"] = len(keys)
    data["gold_count"] = len(gold)
    data["gold_complete"] = bool(keys) and len(gold) == len(keys)
    data["items"] = [{"zotero_key": key, "position": index,
                      "gold": gold.get(key)}
                     for index, key in enumerate(keys)]
    return data


def save_gold(conn: sqlite3.Connection, set_id: int, *, zotero_key: str,
              decision: str, reason: str = "", actor_id: int) -> dict[str, Any]:
    """逐条金标准；仅 draft 期可写（publish 后 409 ``gold_locked``）。"""
    if decision not in DECISIONS:
        raise PilotScreeningError("invalid_decision", params={"decision": decision})
    text = str(reason or "")[:_MAX_REASON]
    key = str(zotero_key)
    with write(conn):
        data = _set_row(conn, set_id)
        if data["status"] != "draft":
            raise PilotScreeningError("gold_locked", params={"status": data["status"]})
        row = conn.execute(
            "SELECT 1 FROM pilot_items WHERE set_id = ? AND zotero_key = ?",
            (int(set_id), key)).fetchone()
        if row is None:
            raise PilotScreeningError("item_not_in_set", params={"zotero_key": key})
        now = _now()
        conn.execute(
            "INSERT INTO pilot_gold(set_id, zotero_key, decision, reason,"
            " decided_by, decided_at) VALUES(?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(set_id, zotero_key) DO UPDATE SET"
            " decision=excluded.decision, reason=excluded.reason,"
            " decided_by=excluded.decided_by, decided_at=excluded.decided_at",
            (int(set_id), key, decision, text, int(actor_id), now))
    current = get_set(conn, set_id)
    gold = {row["zotero_key"]: row["gold"] for row in current["items"]}
    return {"gold": gold.get(key),
            "gold_count": current["gold_count"],
            "item_count": current["item_count"],
            "gold_complete": current["gold_complete"]}


def publish_set(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    """开放训练：要求金标准覆盖全部条目（设计 §六）。"""
    with write(conn):
        data = _set_row(conn, set_id)
        if data["status"] != "draft":
            raise PilotScreeningError("set_not_draft", params={"status": data["status"]})
        item_count = conn.execute(
            "SELECT COUNT(*) FROM pilot_items WHERE set_id = ?",
            (int(set_id),)).fetchone()[0]
        gold_count = conn.execute(
            "SELECT COUNT(*) FROM pilot_gold WHERE set_id = ?",
            (int(set_id),)).fetchone()[0]
        if item_count == 0 or gold_count != item_count:
            raise PilotScreeningError(
                "gold_incomplete", params={"missing": int(item_count - gold_count)})
        conn.execute(
            "UPDATE pilot_sets SET status = 'published', published_at = ?"
            " WHERE id = ?", (_now(), int(set_id)))
    return get_set(conn, set_id)


def archive_set(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    """归档：历史保留、不可再开新尝试（在途尝试仍可续作/交卷）。"""
    with write(conn):
        data = _set_row(conn, set_id)
        if data["status"] == "archived":
            raise PilotScreeningError("set_archived")
        conn.execute(
            "UPDATE pilot_sets SET status = 'archived', archived_at = ?"
            " WHERE id = ?", (_now(), int(set_id)))
    return get_set(conn, set_id)


# ---------------------------------------------------------------------------
# 尝试 / 作答 / 交卷
# ---------------------------------------------------------------------------

def _attempt_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": int(row["id"]), "status": row["status"],
            "started_at": row["started_at"],
            "submitted_at": row["submitted_at"] or ""}


def _open_attempt(conn: sqlite3.Connection, set_id: int,
                  user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM pilot_attempts WHERE set_id = ? AND trainee_user_id = ?"
        " AND status = 'in_progress' ORDER BY id DESC LIMIT 1",
        (int(set_id), int(user_id))).fetchone()
    return dict(row) if row is not None else None


def _latest_attempt(conn: sqlite3.Connection, set_id: int,
                    user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM pilot_attempts WHERE set_id = ? AND trainee_user_id = ?"
        " ORDER BY id DESC LIMIT 1", (int(set_id), int(user_id))).fetchone()
    return dict(row) if row is not None else None


def start_attempt(conn: sqlite3.Connection, set_id: int, *,
                  user_id: int) -> dict[str, Any]:
    """开始/恢复训练：有在途尝试就恢复，否则新开一份独立档案（S6）。"""
    _set_row(conn, set_id)  # 404 先于状态判定
    with write(conn):
        data = _set_row(conn, set_id)
        existing = _open_attempt(conn, set_id, user_id)
        if existing is not None:
            return {"attempt": _attempt_payload(existing), "resumed": True,
                    "set": {key: data[key] for key in
                            ("id", "status", "size", "sampling", "note", "thresholds")}}
        if data["status"] == "archived":
            raise PilotScreeningError("set_archived")
        if data["status"] != "published":
            raise PilotScreeningError("not_published", params={"status": data["status"]})
        cursor = conn.execute(
            "INSERT INTO pilot_attempts(set_id, trainee_user_id, status, started_at)"
            " VALUES(?, ?, 'in_progress', ?)",
            (int(set_id), int(user_id), _now()))
        attempt_id = int(cursor.lastrowid)
    return {"attempt": {"id": attempt_id,
                        "status": "in_progress", "started_at": _now(),
                        "submitted_at": ""},
            "resumed": False,
            "set": {key: data[key] for key in
                    ("id", "status", "size", "sampling", "note", "thresholds")}}


def attempt_queue(conn: sqlite3.Connection, set_id: int, *,
                  user_id: int) -> dict[str, Any]:
    """训练队列（§六 my-attempt）：只含题录字段 + 本人作答，绝不含金标准。"""
    data = _set_row(conn, set_id)
    attempt = _open_attempt(conn, set_id, user_id) or _latest_attempt(
        conn, set_id, user_id)
    if attempt is None:
        raise PilotScreeningError("attempt_not_started")
    answers = {str(row["zotero_key"]): row
               for row in conn.execute(
                   "SELECT zotero_key, decision, reason FROM pilot_decisions"
                   " WHERE attempt_id = ?", (int(attempt["id"]),))}
    items = []
    for index, key in enumerate(_item_keys(conn, set_id)):
        answer = answers.get(key)
        items.append({"zotero_key": key, "position": index,
                      "answer": answer["decision"] if answer else None,
                      "reason": (answer["reason"] or "") if answer else ""})
    return {"attempt": _attempt_payload(attempt),
            "set": {"id": int(data["id"]), "status": data["status"],
                    "size": int(data["size"]), "sampling": data["sampling"],
                    "note": data["note"] or "",
                    "thresholds": data["thresholds"],
                    "item_count": len(items)},
            "items": items,
            "progress": {"total": len(items),
                         "answered": sum(1 for item in items
                                         if item["answer"] is not None)}}


def save_item(conn: sqlite3.Connection, set_id: int, *, user_id: int,
              zotero_key: str, decision: str, reason: str = "",
              attempt_id: int | None = None) -> dict[str, Any]:
    """逐条作答：maybe 在训练里是合法三选项之一（S9 唯一居所）。"""
    if decision not in DECISIONS:
        raise PilotScreeningError("invalid_decision", params={"decision": decision})
    key = str(zotero_key)
    text = str(reason or "")[:_MAX_REASON]
    with write(conn):
        data = _set_row(conn, set_id)
        attempt = _open_attempt(conn, set_id, user_id)
        if attempt is None:
            latest = _latest_attempt(conn, set_id, user_id)
            if latest is not None and latest["status"] == "submitted":
                raise PilotScreeningError("attempt_submitted")
            raise PilotScreeningError("attempt_not_started")
        if attempt_id is not None and int(attempt_id) != int(attempt["id"]):
            raise PilotScreeningError("attempt_mismatch")
        row = conn.execute(
            "SELECT 1 FROM pilot_items WHERE set_id = ? AND zotero_key = ?",
            (int(set_id), key)).fetchone()
        if row is None:
            raise PilotScreeningError("item_not_in_set", params={"zotero_key": key})
        conn.execute(
            "INSERT INTO pilot_decisions(attempt_id, zotero_key, decision, reason,"
            " answered_at) VALUES(?, ?, ?, ?, ?)"
            " ON CONFLICT(attempt_id, zotero_key) DO UPDATE SET"
            " decision=excluded.decision, reason=excluded.reason,"
            " answered_at=excluded.answered_at",
            (int(attempt["id"]), key, decision, text, _now()))
        total = conn.execute(
            "SELECT COUNT(*) FROM pilot_items WHERE set_id = ?",
            (int(set_id),)).fetchone()[0]
        answered = conn.execute(
            "SELECT COUNT(*) FROM pilot_decisions WHERE attempt_id = ?",
            (int(attempt["id"]),)).fetchone()[0]
    return {"saved": True,
            "answer": {"zotero_key": key, "decision": decision, "reason": text},
            "progress": {"total": int(total), "answered": int(answered)}}


def _result_payload(conn: sqlite3.Connection, attempt: Mapping[str, Any]) -> dict:
    row = conn.execute(
        "SELECT * FROM pilot_results WHERE attempt_id = ?",
        (int(attempt["id"]),)).fetchone()
    if row is None:
        raise PilotScreeningError("attempt_not_submitted")
    snapshot = json.loads(row["snapshot_json"] or "{}")
    entered = bool(row["entered_by_discussion"])
    view = dict(snapshot)
    view["entered_by_discussion"] = entered
    # #19：该尝试是否本人本集的首次交卷（练习尝试 False，前端按结构化标记渲染）。
    counting = _first_submitted_id(conn, int(attempt["set_id"]),
                                   int(attempt["trainee_user_id"]))
    counts = counting is not None and counting == int(attempt["id"])
    return {
        "attempt_id": int(attempt["id"]),
        "set_id": int(attempt["set_id"]),
        "status": attempt["status"],
        "submitted_at": attempt["submitted_at"] or "",
        "verdict": row["verdict"],
        "verdict_label_key": "pilot.verdict_" + str(row["verdict"]),
        "metrics": snapshot.get("metrics") or {},
        "thresholds": snapshot.get("thresholds") or dict(DEFAULT_THRESHOLDS),
        "items": snapshot.get("items") or [],
        "entered_by_discussion": entered,
        "counts_for_clearance": counts,
        "practice": not counts,
        "discussion_note": row["discussion_note"] or "",
        "discussed_at": row["discussed_at"] or "",
        "methodology": {"zh": methodology_text(view, lang="zh"),
                        "en": methodology_text(view, lang="en")},
    }


def submit_attempt(conn: sqlite3.Connection, set_id: int, *,
                   user_id: int) -> dict[str, Any]:
    """交卷：全部条目已答 → evaluate 并快照（S4/S8），一次尝试只能交一次。"""
    with write(conn):
        data = _set_row(conn, set_id)
        attempt = _open_attempt(conn, set_id, user_id)
        if attempt is None:
            latest = _latest_attempt(conn, set_id, user_id)
            if latest is not None and latest["status"] == "submitted":
                raise PilotScreeningError("attempt_submitted")
            raise PilotScreeningError("attempt_not_started")
        keys = _item_keys(conn, set_id)
        answers = {str(row["zotero_key"]): {"decision": row["decision"],
                                            "reason": row["reason"] or ""}
                   for row in conn.execute(
                       "SELECT zotero_key, decision, reason FROM pilot_decisions"
                       " WHERE attempt_id = ?", (int(attempt["id"]),))}
        missing = [key for key in keys if key not in answers]
        if missing:
            raise PilotScreeningError("attempt_not_answered",
                                      params={"missing": len(missing)})
        gold = _gold_map(conn, set_id)
        snapshot = evaluate(gold, answers, data["thresholds"],
                            order=keys, sampling=data["sampling"])
        snapshot["set_id"] = int(set_id)
        snapshot["attempt_id"] = int(attempt["id"])
        now = _now()
        conn.execute(
            "INSERT INTO pilot_results(attempt_id, snapshot_json, verdict,"
            " entered_by_discussion, decided_at) VALUES(?, ?, ?, 0, ?)",
            (int(attempt["id"]),
             json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
             snapshot["verdict"], now))
        conn.execute(
            "UPDATE pilot_attempts SET status = 'submitted', submitted_at = ?"
            " WHERE id = ?", (now, int(attempt["id"])))
        submitted = dict(attempt)
        submitted["status"] = "submitted"
        submitted["submitted_at"] = now
    return _result_payload(conn, submitted)


def my_result(conn: sqlite3.Connection, set_id: int, *,
              user_id: int) -> dict[str, Any]:
    """本人**首次**已交卷结果（未交卷 → 409 ``attempt_not_submitted``）。

    #19 裁定「只认首次成绩」：结果视图与方法学报告段只引用首次成绩——
    同集后续交卷是练习（其即时结果随交卷响应给出并标 ``practice=true``，
    历史里可见，但不改写结果视图）。
    """
    _set_row(conn, set_id)
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM pilot_attempts WHERE set_id = ? AND trainee_user_id = ?"
        " AND status = 'submitted' ORDER BY id ASC LIMIT 1",
        (int(set_id), int(user_id)))]
    if not rows:
        raise PilotScreeningError("attempt_not_submitted")
    return _result_payload(conn, rows[0])


def enter_by_discussion(conn: sqlite3.Connection, set_id: int, *,
                        user_id: int, note: str = "") -> dict[str, Any]:
    """临界档"讨论后进入"留痕（S7）：只有 borderline 需要/允许该动作。

    只作用于**首次**已交卷尝试（#19：练习尝试的 borderline 不构成准入）。
    """
    _set_row(conn, set_id)
    attempt = None
    for row in conn.execute(
            "SELECT a.* FROM pilot_attempts a JOIN pilot_results r"
            " ON r.attempt_id = a.id WHERE a.set_id = ? AND a.trainee_user_id = ?"
            " AND a.status = 'submitted' ORDER BY a.id ASC LIMIT 1",
            (int(set_id), int(user_id))):
        attempt = dict(row)
    if attempt is None:
        raise PilotScreeningError("attempt_not_submitted")
    with write(conn):
        verdict = conn.execute(
            "SELECT verdict FROM pilot_results WHERE attempt_id = ?",
            (int(attempt["id"]),)).fetchone()[0]
        if verdict != "borderline":
            raise PilotScreeningError("verdict_not_borderline",
                                      params={"verdict": verdict})
        now = _now()
        conn.execute(
            "UPDATE pilot_results SET entered_by_discussion = 1,"
            " discussion_note = ?, discussed_at = ? WHERE attempt_id = ?",
            (str(note or "")[:_MAX_NOTE], now, int(attempt["id"])))
    return _result_payload(conn, attempt)


def member_state(conn: sqlite3.Connection, *, set_id: int,
                 user_id: int) -> dict[str, Any]:
    """成员在某训练集的最新档案状态（清单/入口显隐用，无金标准字段）。

    键名刻意用 ``attempt_status`` 而不是 ``status``：调用方会把这些键并进
    训练集视图，而集自身已有 ``status``（draft/published/archived）——同名会
    把集生命周期状态覆盖成尝试状态（2.1.5 G2 评审实证缺陷）。
    """
    row = conn.execute(
        "SELECT a.id AS attempt_id, a.status AS attempt_status, r.verdict,"
        " r.entered_by_discussion FROM pilot_attempts a"
        " LEFT JOIN pilot_results r ON r.attempt_id = a.id"
        " WHERE a.set_id = ? AND a.trainee_user_id = ?"
        " ORDER BY a.id DESC LIMIT 1", (int(set_id), int(user_id))).fetchone()
    # #19：计入门的结论只认本集首次交卷（加法键；``verdict`` 仍是最新档案的）。
    first = conn.execute(
        "SELECT r.verdict, r.entered_by_discussion FROM pilot_attempts a"
        " JOIN pilot_results r ON r.attempt_id = a.id"
        " WHERE a.set_id = ? AND a.trainee_user_id = ? AND a.status = 'submitted'"
        " ORDER BY a.id ASC LIMIT 1", (int(set_id), int(user_id))).fetchone()
    clearance = {
        "clearance_verdict": first["verdict"] if first is not None else None,
        "clearance_entered_by_discussion": (
            bool(first["entered_by_discussion"]) if first is not None else False),
    }
    if row is None:
        return {"attempt_id": None, "attempt_status": None, "verdict": None,
                "entered_by_discussion": False, **clearance}
    return {"attempt_id": int(row["attempt_id"]),
            "attempt_status": row["attempt_status"],
            "verdict": row["verdict"],
            "entered_by_discussion": bool(row["entered_by_discussion"]),
            **clearance}


def results_board(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    """负责人看板：每人首次（计入门）+ 最新 + 全部历史（只给结论与指标）。

    #19 裁定「只认首次成绩」：``summary`` 的三档结论计数与每成员的
    ``first``/``clearance_verdict`` 只统计每人**首次** submitted 尝试；
    ``latest`` 与逐条 ``attempts`` 仍完整保留（练习档案带
    ``counts_for_clearance=False`` 标注，不给逐条原答案）。
    """
    _set_row(conn, set_id)
    rows = [dict(row) for row in conn.execute(
        "SELECT a.id AS attempt_id, a.trainee_user_id, a.status, a.started_at,"
        " a.submitted_at, r.snapshot_json, r.verdict, r.entered_by_discussion"
        " FROM pilot_attempts a LEFT JOIN pilot_results r ON r.attempt_id = a.id"
        " WHERE a.set_id = ? ORDER BY a.trainee_user_id, a.id",
        (int(set_id),))]
    # 每成员首次 submitted 尝试 id（结论计数只认它）。
    first_ids: dict[int, int] = {}
    for row in rows:
        if row["status"] == "submitted":
            member_id = int(row["trainee_user_id"])
            attempt_id = int(row["attempt_id"])
            if member_id not in first_ids or attempt_id < first_ids[member_id]:
                first_ids[member_id] = attempt_id
    members: dict[int, dict[str, Any]] = {}
    summary = {"attempts": 0, "submitted": 0, "practice_submitted": 0,
               "passed": 0, "borderline": 0, "failed": 0, "cleared": 0}
    for row in rows:
        summary["attempts"] += 1
        member_id = int(row["trainee_user_id"])
        # #19：练习标记只落在「已交卷且非首次」的行；in_progress 行既不计入门
        # 也不是练习（一旦交卷即正式首考）。
        counts = (row["status"] == "submitted"
                  and first_ids.get(member_id) == int(row["attempt_id"]))
        practice = row["status"] == "submitted" and not counts
        entry = members.setdefault(member_id, {
            "user_id": member_id, "attempts": [], "latest": None,
            "first": None, "clearance_verdict": None, "cleared": False})
        attempt = {"attempt_id": int(row["attempt_id"]),
                   "status": row["status"],
                   "started_at": row["started_at"],
                   "submitted_at": row["submitted_at"] or "",
                   "verdict": row["verdict"],
                   "entered_by_discussion": bool(row["entered_by_discussion"]),
                   "counts_for_clearance": bool(counts),
                   "practice": bool(practice)}
        if row["snapshot_json"]:
            snapshot = json.loads(row["snapshot_json"] or "{}")
            metrics = snapshot.get("metrics") or {}
            attempt.update({
                "kappa_binary": metrics.get("kappa_binary"),
                "kappa_raw": metrics.get("kappa_raw"),
                "agreement": metrics.get("agreement"),
                "pabak": metrics.get("pabak"),
                "thresholds": snapshot.get("thresholds") or dict(DEFAULT_THRESHOLDS),
            })
        entry["attempts"].append(attempt)
        if row["status"] == "submitted":
            summary["submitted"] += 1
            if not counts:
                summary["practice_submitted"] += 1
            entry["latest"] = attempt
            if counts:
                # 方法学结论只认首次交卷（#19）。
                entry["first"] = attempt
                verdict = row["verdict"]
                entry["clearance_verdict"] = verdict
                if verdict == "pass":
                    summary["passed"] += 1
                elif verdict == "borderline":
                    summary["borderline"] += 1
                elif verdict == "fail":
                    summary["failed"] += 1
                if verdict == "pass" or attempt["entered_by_discussion"]:
                    entry["cleared"] = True
                    summary["cleared"] += 1
    return {"members": [members[key] for key in sorted(members)],
            "summary": summary}


def member_history(conn: sqlite3.Connection, *, user_id: int) -> list[dict[str, Any]]:
    """本人全部训练尝试（跨训练集，最新在前）——进步对比用。

    #19：每行带 ``counts_for_clearance``（本集首次交卷才 True）与
    ``practice``（练习标记，前端用词典键 ``pilot.attempt_practice`` 渲染
    「练习，不计入门」）；练习尝试照常列出（保留练习价值）。
    """
    rows = conn.execute(
        "SELECT a.id AS attempt_id, a.set_id, a.status, a.started_at,"
        " a.submitted_at, s.status AS set_status, s.sampling, s.size,"
        " r.snapshot_json, r.verdict, r.entered_by_discussion"
        " FROM pilot_attempts a JOIN pilot_sets s ON s.id = a.set_id"
        " LEFT JOIN pilot_results r ON r.attempt_id = a.id"
        " WHERE a.trainee_user_id = ? ORDER BY a.id DESC", (int(user_id),)).fetchall()
    first_ids: dict[int, int] = {}
    for row in rows:
        if row["status"] == "submitted":
            set_id = int(row["set_id"])
            attempt_id = int(row["attempt_id"])
            if set_id not in first_ids or attempt_id < first_ids[set_id]:
                first_ids[set_id] = attempt_id
    history = []
    for row in rows:
        attempt_id = int(row["attempt_id"])
        # #19：练习标记只对「已交卷且非首次」为 True；in_progress 行
        # counts_for_clearance=False 且 practice=False（交卷后才定性）。
        counts = (row["status"] == "submitted"
                  and first_ids.get(int(row["set_id"])) == attempt_id)
        practice = row["status"] == "submitted" and not counts
        item = {"attempt_id": attempt_id, "set_id": int(row["set_id"]),
                "status": row["status"], "set_status": row["set_status"],
                "sampling": row["sampling"], "size": int(row["size"]),
                "started_at": row["started_at"],
                "submitted_at": row["submitted_at"] or "",
                "verdict": row["verdict"],
                "entered_by_discussion": bool(row["entered_by_discussion"]),
                "counts_for_clearance": bool(counts),
                "practice": bool(practice),
                "kappa_binary": None, "agreement": None, "pabak": None}
        if row["snapshot_json"]:
            metrics = (json.loads(row["snapshot_json"] or "{}").get("metrics") or {})
            item["kappa_binary"] = metrics.get("kappa_binary")
            item["agreement"] = metrics.get("agreement")
            item["pabak"] = metrics.get("pabak")
        history.append(item)
    return history


# ---------------------------------------------------------------------------
# enforce_pass 拦截（S2）：正式初筛写入口的三态判定
# ---------------------------------------------------------------------------

def _first_submitted_id(conn: sqlite3.Connection, set_id: int,
                        user_id: int) -> int | None:
    """某成员在某训练集的**首次已交卷尝试** id（无交卷 → None）。

    终审 #19 裁定「只认首次成绩」：准入（cleared/enforce）、负责人看板的
    方法学结论与方法学报告段一律以这个尝试为准；后续尝试是练习档案，
    在历史里以 ``counts_for_clearance=False`` 标注。
    """
    row = conn.execute(
        "SELECT MIN(id) FROM pilot_attempts"
        " WHERE set_id = ? AND trainee_user_id = ? AND status = 'submitted'",
        (int(set_id), int(user_id))).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else None


def cleared(conn: sqlite3.Connection, *, user_id: int) -> bool:
    """本任务是否已有"达标或已留痕讨论进入"记录（跨训练集）。

    只认每套题的**首次** submitted 尝试（终审 #19）：交卷揭示金标准后
    同集照抄重得的 pass 不再解锁 enforce 闸。
    """
    row = conn.execute(
        "SELECT 1 FROM pilot_results r JOIN pilot_attempts a"
        " ON a.id = r.attempt_id"
        " WHERE a.trainee_user_id = ?"
        " AND a.id = (SELECT MIN(a2.id) FROM pilot_attempts a2"
        "  WHERE a2.set_id = a.set_id AND a2.trainee_user_id = a.trainee_user_id"
        "  AND a2.status = 'submitted')"
        " AND (r.verdict = 'pass' OR r.entered_by_discussion = 1) LIMIT 1",
        (int(user_id),)).fetchone()
    return row is not None


def enforce_blocked(conn: sqlite3.Connection, *, user_id: int,
                    owner_user_id: int | None) -> tuple[bool, int | None]:
    """(是否拦截, 被要求的训练集 id)。

    仅在「pilot 开启 + enforce 开启 + 成员未达标/未留痕 + 存在已发布训练集」
    时拦截；负责人本人豁免；无已发布集时优雅放行（避免无训练可做却被拦）。
    """
    settings = get_settings(conn)
    if not (settings["pilot_enabled"] and settings["enforce_pass"]):
        return False, None
    if owner_user_id is not None and int(user_id) == int(owner_user_id):
        return False, None
    row = conn.execute(
        "SELECT id FROM pilot_sets WHERE status = 'published'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return False, None
    if cleared(conn, user_id=user_id):
        return False, None
    return True, int(row["id"])
