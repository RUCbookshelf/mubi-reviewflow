"""S3 双语混合 AL 受控实验:机翻 SYNERGY 子集 + S0 引擎回放 + 机制分析。

设计文档:``research/notes/bilingual_design.md``(定位、协议、局限)。
数据事实:``research/data/chinese_recon_memo.md``(无公开中文标注集 ⇒
机翻 SYNERGY 子集受控实验;结论只限"翻译域",不可外推真实中文场景)。

构成(同 keys / 同顺序 / 同标签 / 同先验位置):

- ``en``     原始(纯英文);
- ``zh``     全部 title+abstract 机翻(argostranslate en→zh,贪心解码 ⇒ 确定性);
- ``mixed``  固定分割种子随机抽 50% 机翻(分割与 AL 种子正交);
- ``mixed_langtag``  mixed + title 前置语言标记 token(langzh/langen);
- ``mixed_entitle``  mixed 的 zh 半仅翻 abstract、title 保留英文(共享锚特征)。

回放协议:``run_config(preset="product", strategy="max", priors="asreview", seed)``
(alpha=1.0、warmup=5、逐步 refit、无 balance + ASReview 1+1 先验;max 无先验时
完全确定,种子必须经由先验起作用)。同种子同条件两遍回放指标与轨迹逐位相等
(:func:`assert_reproducible`)。

机制量:词表规模/可加性、跨语言 token 交集、每文档非零特征数、静态盲区
(zh 半质量在 en 半词表下的 OOV 率)、轨迹盲区(按已筛标签词表逐 checkpoint
的剩余队列 OOV 质量与"盲文档"占比)、relevant 记录发现位次对照。

只读引用 ``research/sim/replay.py`` 与 ``coscreen.al.features``;不改产品代码。
CLI::

    ./.venv/bin/python research/sim/bilingual.py pilot    # 全流程(翻译→回放→机制→落盘)
    ./.venv/bin/python research/sim/bilingual.py spotcheck # 翻译质量抽查样例输出
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coscreen.al.features import tokenize  # noqa: E402  (只读:产品分词路径)
from replay import (  # noqa: E402  (只读:S0 快速回放引擎)
    FastRecord,
    ReplayResult,
    _article_text,
    run_config,
    load_dataset_csv,
)

__all__ = [
    "MT_SPLIT_SEED", "DATASETS", "SEEDS", "CONDITION_ORDER",
    "Condition", "fake_en_zh", "make_argos_translator", "TranslationCache",
    "zh_half_keys", "build_conditions", "doc_counters", "corpus_stats",
    "static_blindness", "trajectory_blindness", "relevant_positions",
    "run_one", "assert_reproducible", "run_pilot", "spot_check",
]

#: mixed 构成的机翻分割种子(与 AL 种子正交,固定 ⇒ 各 AL 种子共享同一池构成)。
MT_SPLIT_SEED = 2026
#: 试点数据集(SYNERGY 小集,≤4k;见设计文档 §2)。
DATASETS = ("Nelson_2002", "Oud_2018")
#: 试点 AL 种子(经由 asreview 先验起作用)。
SEEDS = (42, 43, 44, 45, 46)
#: 条件输出顺序。
CONDITION_ORDER = ("en", "zh", "mixed", "mixed_langtag", "mixed_entitle")

_PILOT_DIR = PROJECT_ROOT / "research" / "results" / "s3_pilot"
_TAG_ZH, _TAG_EN = "langzh", "langen"
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# 翻译:argos(真实)/ fake(测试与降级演示,确定性伪机翻)
# ---------------------------------------------------------------------------

def _clean_for_mt(text: str) -> str:
    """翻译输入清洗:去 HTML 标签、压空白(SYNERGY 摘要含 <h2> 等标记)。"""
    return _WS.sub(" ", _HTML_TAG.sub(" ", text)).strip()


def fake_en_zh(text: str) -> str:
    """确定性伪机翻:拉丁词 → 哈希映射的两个 CJK 字符(数字保留)。

    仅用于测试与降级演示(占位实验语义:模拟"特征空间整体替换"的 OOV 膨胀),
    **不产生任何真实中文语义**;用它的结果一律不得表述为翻译语料结论。
    """
    out: list[str] = []
    for m in re.finditer(r"\d+|[A-Za-z]+|[^A-Za-z\d\s]+|\s+", text):
        tok = m.group(0)
        if tok.isspace():
            out.append(" ")
        elif tok.isdigit():
            out.append(tok)
        elif tok.isalpha():
            h = int(hashlib.md5(tok.lower().encode("utf-8")).hexdigest(), 16)
            out.append(chr(0x4E00 + h % 2000) + chr(0x4E00 + (h // 2000) % 2000))
        else:
            out.append(tok)
    return "".join(out)


def make_argos_translator():
    """返回 en→zh 翻译函数;首次调用时惰性校验/安装 argos 包(离线推理)。"""
    from argostranslate import package, translate  # noqa: import 在调用时才发生

    state: dict[str, object] = {}

    def translate_text(text: str) -> str:
        if not state:
            installed = [p for p in package.get_installed_packages()
                         if p.from_code == "en" and p.to_code == "zh"]
            if not installed:
                package.update_package_index()
                cands = [p for p in package.get_available_packages()
                         if p.from_code == "en" and p.to_code == "zh"]
                if not cands:
                    raise RuntimeError(
                        "argos 模型索引中无 en→zh 包;网络不可用时改用降级方案")
                pkg = cands[0]
                t0 = time.perf_counter()
                local = pkg.download()
                package.install_from_path(local)
                print(f"[argos] en→zh 模型下载+安装完成 "
                      f"({time.perf_counter() - t0:.0f}s)", flush=True)
            state["fn"] = translate
        tr = state["fn"]
        return tr.translate(text, "en", "zh")

    return translate_text


class TranslationCache:
    """sha256(text 原文) → 译文的落盘缓存(确定性 ⇒ 重跑不重翻、逐位一致)。"""

    def __init__(self, path: str | Path, translate_fn) -> None:
        self.path = Path(path)
        self.translate_fn = translate_fn
        self._new = 0
        if self.path.exists():
            self.d: dict[str, str] = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.d = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, text: str) -> str:
        if not text or not text.strip():
            return text
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        hit = self.d.get(key)
        if hit is not None:
            return hit
        out = self.translate_fn(_clean_for_mt(text))
        self.d[key] = out
        self._new += 1
        if self._new % 20 == 0:
            self.flush()
        return out

    def flush(self) -> None:
        self.path.write_text(
            json.dumps(self.d, ensure_ascii=False, indent=0), encoding="utf-8")


# ---------------------------------------------------------------------------
# 语料构造:三构成 + 两缓解臂(同 keys/顺序/标签;无泄漏)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Condition:
    """一个语言构成条件:记录、标签、zh 半 key 集、描述元数据。"""

    name: str
    records: list
    gold: dict[str, str]
    zh_keys: frozenset
    meta: dict

    @property
    def en_keys(self) -> frozenset:
        all_k = {r.zotero_key for r in self.records}
        return frozenset(all_k - set(self.zh_keys))


def zh_half_keys(records: list, frac: float = 0.5,
                 seed: int = MT_SPLIT_SEED) -> frozenset:
    """固定种子的 50% 机翻分割(按 key 无放回抽样;与 AL 种子正交)。"""
    keys = [r.zotero_key for r in records]
    k = int(round(len(keys) * frac))
    return frozenset(random.Random(seed).sample(keys, k))


def _mt_record(rec: FastRecord, t_title, t_abstract, *, keep_title: bool = False,
               tag: str | None = None) -> FastRecord:
    title = rec.title if keep_title else t_title(rec.title)
    if tag is not None:
        title = f"{tag} {title}".strip()
    return replace(rec, title=title, abstract=t_abstract(rec.abstract))


def build_conditions(records: list, gold: dict[str, str], t_title, t_abstract,
                     frac: float = 0.5, split_seed: int = MT_SPLIT_SEED
                     ) -> dict[str, Condition]:
    """构造全部条件。

    不变量(测试断言,这里先行自检):keys 全同有序、gold 逐键相同、
    mixed 每条记录恰出现一次,en 半文本与原文逐位相同。
    """
    keys = [r.zotero_key for r in records]
    assert len(set(keys)) == len(keys), "记录键必须唯一"
    zh_half = zh_half_keys(records, frac, split_seed)

    en_records = [replace(r) for r in records]
    zh_records = [_mt_record(r, t_title, t_abstract) for r in records]
    mixed_records = [
        _mt_record(r, t_title, t_abstract) if r.zotero_key in zh_half
        else replace(r)
        for r in records
    ]
    langtag_records = [
        _mt_record(r, t_title, t_abstract, tag=_TAG_ZH if r.zotero_key in zh_half
                   else _TAG_EN) if r.zotero_key in zh_half
        else _mt_record(r, lambda x: x, lambda x: x, tag=_TAG_EN)
        for r in records
    ]
    entitle_records = [
        _mt_record(r, t_title, t_abstract, keep_title=True)
        if r.zotero_key in zh_half else replace(r)
        for r in records
    ]

    n_rel = sum(1 for v in gold.values() if v == "include")
    rel_zh = sum(1 for k in keys if k in zh_half and gold.get(k) == "include")
    base_meta = {
        "frac_zh": frac, "split_seed": split_seed,
        "n_zh_half": len(zh_half),
        "n_relevant_zh_half": rel_zh,
        "n_relevant_en_half": n_rel - rel_zh,
    }
    conds = {
        "en": Condition("en", en_records, dict(gold), frozenset(),
                        {**base_meta, "desc": "原始纯英文"}),
        "zh": Condition("zh", zh_records, dict(gold),
                        frozenset(keys), {**base_meta, "desc": "全机翻纯中文"}),
        "mixed": Condition("mixed", mixed_records, dict(gold), zh_half,
                           {**base_meta, "desc": "50% 机翻混合"}),
        "mixed_langtag": Condition(
            "mixed_langtag", langtag_records, dict(gold), zh_half,
            {**base_meta, "desc": "mixed + 语言标记 token"}),
        "mixed_entitle": Condition(
            "mixed_entitle", entitle_records, dict(gold), zh_half,
            {**base_meta, "desc": "mixed, zh 半仅翻摘要、题名保留英文"}),
    }
    # 构造器自检:键序/标签/无泄漏/比例。
    for c in conds.values():
        assert [r.zotero_key for r in c.records] == keys, c.name
        assert c.gold == gold, c.name
        assert len(c.records) == len(records), c.name
    mx = conds["mixed"]
    assert set(mx.zh_keys) | mx.en_keys == set(keys)
    assert not (set(mx.zh_keys) & mx.en_keys)
    assert len(mx.zh_keys) == int(round(len(keys) * frac))
    for k in keys:
        if k not in zh_half:
            assert mx.records[keys.index(k)].title == records[keys.index(k)].title
            assert (mx.records[keys.index(k)].abstract
                    == records[keys.index(k)].abstract)
    return conds


# ---------------------------------------------------------------------------
# 机制分析量
# ---------------------------------------------------------------------------

def doc_counters(records: list) -> dict[str, Counter]:
    """每文档 token 计数(product tokenize + 四字段拼接,与引擎同一路径)。"""
    return {r.zotero_key: Counter(tokenize(_article_text(r))) for r in records}


def corpus_stats(counters: dict[str, Counter]) -> dict:
    vocab = set()
    for cnt in counters.values():
        vocab.update(cnt)
    nnz = [len(c) for c in counters.values()]
    mass = sum(sum(c.values()) for c in counters.values())
    return {"V": len(vocab), "n_docs": len(counters), "token_mass": mass,
            "nnz_mean": (sum(nnz) / len(nnz)) if nnz else 0.0,
            "nnz_max": max(nnz) if nnz else 0}


def static_blindness(counters: dict[str, Counter], vocab: set) -> dict:
    """参照词表 vocab 下的 token 质量 OOV 率 + 盲文档(≥95% 质量 OOV)占比。"""
    mass = oov = 0
    blind = 0
    for cnt in counters.values():
        m = sum(cnt.values())
        seen = sum(v for t, v in cnt.items() if t in vocab)
        mass += m
        oov += m - seen
        if m > 0 and (m - seen) / m >= 0.95:
            blind += 1
    return {"token_mass": mass, "oov_mass_rate": (oov / mass) if mass else 0.0,
            "n_blind": blind, "n_docs": len(counters),
            "blind_frac": (blind / len(counters)) if counters else 0.0}


def trajectory_blindness(counters: dict[str, Counter], zh_keys: frozenset,
                         trajectory: list[dict], checkpoint_every: int = 25,
                         warmup: int = 5) -> dict:
    """product refit 机制下的轨迹盲区:逐 checkpoint 以"已筛标签词表"为参照。

    每个 checkpoint(每 checkpoint_every 条 + 最后一步)统计剩余队列的
    token 质量 OOV 率与盲文档占比(全量 + 分通道)。盲文档在引擎中得分
    恰为类先验(无任何已见词项),排序保持既有相对顺序 ⇒ 模型信息为零。
    """
    order = [row["record_id"] for row in trajectory]
    n = len(order)
    if n == 0:
        return {}
    all_keys = list(counters)
    checkpoints = sorted(set(list(range(warmup, n + 1, checkpoint_every)) + [n]))
    rows = []
    for k in checkpoints:
        labeled_vocab: set = set()
        for key in order[:k]:
            labeled_vocab.update(counters[key])
        remaining = [key for key in all_keys if key not in set(order[:k])]
        sub_all = {key: counters[key] for key in remaining}
        sub_zh = {key: counters[key] for key in remaining
                  if key in zh_keys}
        sub_en = {key: counters[key] for key in remaining
                  if key not in zh_keys}
        st_all = static_blindness(sub_all, labeled_vocab)
        st_zh = static_blindness(sub_zh, labeled_vocab)
        st_en = static_blindness(sub_en, labeled_vocab)
        rows.append({
            "k": k, "vocab_labeled": len(labeled_vocab),
            "oov_mass_rate": st_all["oov_mass_rate"],
            "oov_mass_rate_zh": st_zh["oov_mass_rate"],
            "oov_mass_rate_en": st_en["oov_mass_rate"],
            "blind_frac": st_all["blind_frac"],
            "blind_frac_zh": st_zh["blind_frac"],
            "blind_frac_en": st_en["blind_frac"],
        })
    model_rows = rows  # checkpoints 从 warmup 起 ⇒ 全部属模型驱动阶段
    return {
        "checkpoints": rows,
        "mean_oov_mass_rate": sum(r["oov_mass_rate"] for r in model_rows) / len(model_rows),
        "mean_oov_mass_rate_zh": sum(r["oov_mass_rate_zh"] for r in model_rows) / len(model_rows),
        "mean_oov_mass_rate_en": sum(r["oov_mass_rate_en"] for r in model_rows) / len(model_rows),
        "mean_blind_frac": sum(r["blind_frac"] for r in model_rows) / len(model_rows),
        "mean_blind_frac_zh": sum(r["blind_frac_zh"] for r in model_rows) / len(model_rows),
        "mean_blind_frac_en": sum(r["blind_frac_en"] for r in model_rows) / len(model_rows),
    }


def relevant_positions(result: ReplayResult) -> dict[str, int]:
    """relevant 记录的发现位次(1 起;来自轨迹)。"""
    return {row["record_id"]: row["i"] for row in result.trajectory
            if row["label"] == "include"}


# ---------------------------------------------------------------------------
# 回放
# ---------------------------------------------------------------------------

def run_one(cond: Condition, seed: int, *, log_dir: str | Path | None = None,
            log_prefix: str | None = None) -> ReplayResult:
    """试点协议:product 语义 + ASReview 1+1 先验 + max 策略(设计文档 §4)。"""
    return run_config(
        records=cond.records, gold=cond.gold,
        preset="product", strategy="max", priors="asreview", seed=seed,
        top_k_log=3, log_dir=log_dir, log_prefix=log_prefix,
    )


def assert_reproducible(cond: Condition, seed: int) -> ReplayResult:
    """同种子同条件两遍回放:指标与轨迹逐位相等(可复现断言)。

    轨迹中的 ``step_elapsed_s`` 是墙钟计时,天然逐次不同 ⇒ 断言前剥离
    (其余字段——含分数、排序、指标——必须逐位相等)。
    """

    def _strip(result: ReplayResult) -> str:
        rows = [{k: v for k, v in row.items() if k != "step_elapsed_s"}
                for row in result.trajectory]
        return json.dumps(rows)

    r1 = run_one(cond, seed)
    r2 = run_one(cond, seed)
    assert r1.metrics_dict() == r2.metrics_dict(), "指标不可复现"
    assert _strip(r1) == _strip(r2), "轨迹不可复现"
    return r1


# ---------------------------------------------------------------------------
# 试点主流程
# ---------------------------------------------------------------------------

def _agg(rows: list[dict]) -> list[dict]:
    """按 (dataset, condition) 聚合 mean/sd(5 种子)。"""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["dataset"], row["condition"]), []).append(row)
    out = []
    for (ds, cond), rs in sorted(groups.items()):
        agg: dict = {"dataset": ds, "condition": cond, "n_seeds": len(rs),
                     "n_records": rs[0]["n_records"],
                     "n_relevant": rs[0]["n_relevant"]}
        for f in ("n_to_95", "wss95", "r95", "ttd_mean", "elapsed_s"):
            vals = [r[f] for r in rs]
            mean = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
            agg[f"{f}_mean"], agg[f"{f}_sd"] = mean, sd
        out.append(agg)
    return out


def run_pilot(datasets=DATASETS, seeds=SEEDS, out_dir: Path = _PILOT_DIR,
              translate_fn=None) -> dict:
    """全流程:翻译(缓存)→ 构成 → 回放(30 主 + 20 缓解臂)→ 机制 → 落盘。"""
    out_dir = Path(out_dir)
    (out_dir / "trajectories").mkdir(parents=True, exist_ok=True)
    if translate_fn is None:
        translate_fn = make_argos_translator()
    cache = TranslationCache(out_dir / "mt_cache.json", translate_fn)
    t_title = lambda s: cache(s)  # noqa: E731
    t_abstract = lambda s: cache(s)  # noqa: E731

    all_rows: list[dict] = []
    mechanism: dict = {}
    rel_pos_all: dict[str, dict] = {}
    for ds in datasets:
        csv_path = PROJECT_ROOT / "research" / "data" / "synergy" / f"{ds}.csv"
        records, gold = load_dataset_csv(csv_path)
        t0 = time.perf_counter()
        conds = build_conditions(records, gold, t_title, t_abstract)
        cache.flush()
        print(f"[{ds}] n={len(records)} 翻译+构造 {time.perf_counter()-t0:.0f}s "
              f"(缓存新增 {cache._new} 条)", flush=True)

        # 可复现断言(mixed, seed=42):两遍逐位一致。
        assert_reproducible(conds["mixed"], seeds[0])

        rel_pos: dict[str, dict[str, int]] = {}
        counters_per_cond = {name: doc_counters(c.records)
                             for name, c in conds.items()}
        for name in CONDITION_ORDER:
            cond = conds[name]
            for seed in seeds:
                r = run_one(cond, seed,
                            log_dir=out_dir / "trajectories",
                            log_prefix=f"{ds}-{name}-s{seed}")
                all_rows.append({
                    "dataset": ds, "condition": name, "seed": seed,
                    **r.metrics_dict(),
                    "n_trained_steps": r.n_trained_steps,
                    "elapsed_s": round(r.elapsed_s, 3),
                })
                if name in ("en", "zh", "mixed"):
                    rel_pos[name] = relevant_positions(r)
            print(f"[{ds}] {name}: " + " ".join(
                f"wss95={row['wss95']:.4f}" for row in all_rows
                if row["dataset"] == ds and row["condition"] == name), flush=True)

        # ---- 机制量 ----
        st_en_c = counters_per_cond["en"]
        st_zh_c = counters_per_cond["zh"]
        vocab_en = set().union(*st_en_c.values()) if st_en_c else set()
        vocab_zh = set().union(*st_zh_c.values()) if st_zh_c else set()
        mixed_counters = counters_per_cond["mixed"]
        en_half_counters = {k: c for k, c in mixed_counters.items()
                            if k not in conds["mixed"].zh_keys}
        zh_half_counters = {k: c for k, c in mixed_counters.items()
                            if k in conds["mixed"].zh_keys}
        traj = {name: None for name in CONDITION_ORDER}
        for name in ("en", "zh", "mixed", "mixed_entitle"):
            # 轨迹盲区只需主条件 + entitle(缓解效果);en/zh 条件 zh_keys 语义:
            # en 条件无 zh 文档 ⇒ zh 通道空;zh 条件全 zh。
            zh_keys = conds[name].zh_keys
            traj[name] = trajectory_blindness(
                counters_per_cond[name], zh_keys,
                [row for row in _load_traj(out_dir, ds, name, seeds[0])],
                warmup=5)
        mech = {
            "vocab": {
                "en": corpus_stats(st_en_c), "zh": corpus_stats(st_zh_c),
                "mixed": corpus_stats(mixed_counters),
                "mixed_langtag": corpus_stats(counters_per_cond["mixed_langtag"]),
                "overlap_size": len(vocab_en & vocab_zh),
                "overlap_tokens": sorted(vocab_en & vocab_zh)[:50],
                "additivity_pred_V": len(vocab_en) + len(vocab_zh)
                - len(vocab_en & vocab_zh),
            },
            "nnz": {
                "en_cond_mean": corpus_stats(st_en_c)["nnz_mean"],
                "zh_cond_mean": corpus_stats(st_zh_c)["nnz_mean"],
                "mixed_en_half_mean": corpus_stats(en_half_counters)["nnz_mean"],
                "mixed_zh_half_mean": corpus_stats(zh_half_counters)["nnz_mean"],
            },
            "static_blindness": {
                # 静态:zh 半质量在"en 条件全词表"下的 OOV 率(训练全英文时的
                # 不可见度),及对称方向(en 半质量在 zh 全词表下)。
                "zh_half_under_en_vocab": static_blindness(
                    zh_half_counters, vocab_en),
                "en_half_under_zh_vocab": static_blindness(
                    en_half_counters, vocab_zh),
            },
            "trajectory_blindness": traj,
            "cond_meta": {name: conds[name].meta for name in CONDITION_ORDER},
        }
        mechanism[ds] = mech
        rel_pos_all[ds] = {
            "zh_half": sorted(conds["mixed"].zh_keys),
            "positions": rel_pos,
            "gold": gold,
        }

    # ---- 落盘 ----
    agg = _agg(all_rows)
    (out_dir / "pilot_runs.json").write_text(json.dumps(
        {"protocol": {"preset": "product", "strategy": "max",
                      "priors": "asreview", "seeds": list(seeds),
                      "datasets": list(datasets),
                      "mt_split_seed": MT_SPLIT_SEED},
         "runs": all_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "pilot_summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0]))
        w.writeheader()
        w.writerows(all_rows)
    with (out_dir / "pilot_agg.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(agg[0]))
        w.writeheader()
        w.writerows(agg)
    (out_dir / "mechanism_stats.json").write_text(json.dumps(
        mechanism, ensure_ascii=False, indent=2), encoding="utf-8")
    for ds, payload in rel_pos_all.items():
        with (out_dir / f"rel_positions_{ds}.csv").open(
                "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["record_id", "in_zh_half", "label",
                        "pos_en", "pos_zh", "pos_mixed"])
            for key, gold_lab in payload["gold"].items():
                if gold_lab != "include":
                    continue
                p = payload["positions"]
                w.writerow([key, key in set(payload["zh_half"]), gold_lab,
                            p.get("en", {}).get(key, ""),
                            p.get("zh", {}).get(key, ""),
                            p.get("mixed", {}).get(key, "")])
    cache.flush()
    print(f"[pilot] 完成:{len(all_rows)} 次回放,输出在 {out_dir}", flush=True)
    return {"rows": all_rows, "agg": agg, "mechanism": mechanism}


def _load_traj(out_dir: Path, ds: str, name: str, seed: int) -> list[dict]:
    """读回刚写出的轨迹(供机制分析;避免引擎额外返回开销)。"""
    path = out_dir / "trajectories" / f"{ds}-{name}-s{seed}.trajectory.jsonl"
    if not path.exists():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 翻译质量抽查样例
# ---------------------------------------------------------------------------

def spot_check(datasets=DATASETS, k_per_label: int = 5, seed: int = 0,
               out_dir: Path = _PILOT_DIR, translate_fn=None) -> Path:
    """每集抽 include/exclude 各 k 条,输出 原文/译文 对照 markdown(供判读)。"""
    out_dir = Path(out_dir)
    if translate_fn is None:
        translate_fn = make_argos_translator()
    cache = TranslationCache(out_dir / "mt_cache.json", translate_fn)
    lines = ["# S3 机翻质量抽查样例(argos en→zh;供判读)\n"]
    for ds in datasets:
        records, gold = load_dataset_csv(
            PROJECT_ROOT / "research" / "data" / "synergy" / f"{ds}.csv")
        rng = random.Random(seed)
        picks: list = []
        for lab in ("include", "exclude"):
            pool = [r for r in records
                    if gold[r.zotero_key] == lab and (r.title or r.abstract)]
            picks += rng.sample(pool, min(k_per_label, len(pool)))
        lines.append(f"\n## {ds}(include {k_per_label} + exclude {k_per_label})\n")
        for r in picks:
            src = _clean_for_mt(r.title + " || " + (r.abstract or ""))[:600]
            mt = cache(r.title) + " || " + cache(r.abstract or "")
            lines.append(f"### {r.zotero_key} [{gold[r.zotero_key]}]\n")
            lines.append(f"- 原文: {src}\n")
            lines.append(f"- 译文: {mt[:600]}\n")
    cache.flush()
    path = out_dir / "mt_spotcheck.md"
    path.write_text("".join(lines), encoding="utf-8")
    print(f"[spotcheck] 写出 {path}", flush=True)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "pilot"
    if cmd == "pilot":
        run_pilot()
    elif cmd == "spotcheck":
        spot_check()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
