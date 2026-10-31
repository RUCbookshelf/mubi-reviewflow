"""S3 双语实验测试:构造器不变量 / 回放可复现 / 机翻确定性 / 机制量。

全部测试使用确定性伪机翻 ``fake_en_zh``(stub),不依赖 argos 模型安装;
真实机翻的确定性由"同输入同输出"断言(stub 与缓存路径)覆盖,argos
本身的确定性由其贪心解码保证,并在试点运行中经缓存复验(重跑零新增翻译)。
端到端 mini 试点读取真实 SYNERGY CSV(只读),输出写入 pytest tmp 目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SIM_DIR = Path(__file__).resolve().parent
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))

import bilingual as bl  # noqa: E402
from replay import make_synthetic_corpus  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def small_corpus():
    records, gold = make_synthetic_corpus(80, seed=7)
    return records, gold


def _build_all(records, gold, frac=0.5, split_seed=1234):
    return bl.build_conditions(
        records, gold, bl.fake_en_zh, bl.fake_en_zh,
        frac=frac, split_seed=split_seed)


# ---------------------------------------------------------------------------
# 混合构造器:比例 / 无泄漏 / 标签不变 / 顺序不变
# ---------------------------------------------------------------------------

def test_conditions_share_keys_order_labels(small_corpus):
    records, gold = small_corpus
    conds = _build_all(records, gold)
    keys = [r.zotero_key for r in records]
    for name, c in conds.items():
        assert [r.zotero_key for r in c.records] == keys, name
        assert c.gold == gold, name
        assert len(c.records) == len(records), name


def test_mixed_fraction_disjoint_exhaustive(small_corpus):
    records, gold = small_corpus
    mx = _build_all(records, gold)["mixed"]
    keys = {r.zotero_key for r in records}
    assert len(mx.zh_keys) == int(round(len(keys) * 0.5))
    assert set(mx.zh_keys) | mx.en_keys == keys      # 无遗漏
    assert not (set(mx.zh_keys) & mx.en_keys)        # 无泄漏(互斥)
    assert len({r.zotero_key for r in mx.records}) == len(records)  # 恰一次


def test_mixed_en_half_bitwise_unchanged_zh_half_translated(small_corpus):
    records, gold = small_corpus
    mx = _build_all(records, gold)["mixed"]
    by_key = {r.zotero_key: r for r in mx.records}
    for r in records:
        if r.zotero_key in mx.zh_keys:
            if r.title.strip():
                assert by_key[r.zotero_key].title != r.title
            if r.abstract.strip():
                assert by_key[r.zotero_key].abstract != r.abstract
        else:
            assert by_key[r.zotero_key].title == r.title
            assert by_key[r.zotero_key].abstract == r.abstract


def test_zh_condition_translates_all(small_corpus):
    records, gold = small_corpus
    zh = _build_all(records, gold)["zh"]
    assert zh.zh_keys == {r.zotero_key for r in records}
    for r_src, r_mt in zip(records, zh.records):
        assert r_src.zotero_key == r_mt.zotero_key
        if r_src.title.strip():
            assert r_mt.title != r_src.title


def test_langtag_and_entitle_arms(small_corpus):
    records, gold = small_corpus
    conds = _build_all(records, gold)
    langtag = conds["mixed_langtag"]
    entitle = conds["mixed_entitle"]
    by_lt = {r.zotero_key: r for r in langtag.records}
    by_et = {r.zotero_key: r for r in entitle.records}
    for r in records:
        k = r.zotero_key
        # 语言标记:zh 半 langzh、en 半 langen,且为 title 首个 token
        if k in langtag.zh_keys:
            assert by_lt[k].title.split(" ", 1)[0] == bl._TAG_ZH
        else:
            assert by_lt[k].title.split(" ", 1)[0] == bl._TAG_EN
        # entitle:zh 半 title 保留英文原文,abstract 被翻译
        if k in entitle.zh_keys:
            assert by_et[k].title == r.title
            if r.abstract.strip():
                assert by_et[k].abstract != r.abstract
        else:
            assert by_et[k].abstract == r.abstract


def test_split_seed_deterministic_and_orthogonal(small_corpus):
    records, gold = small_corpus
    a = _build_all(records, gold, split_seed=1234)["mixed"].zh_keys
    b = _build_all(records, gold, split_seed=1234)["mixed"].zh_keys
    c = _build_all(records, gold, split_seed=999)["mixed"].zh_keys
    assert a == b                       # 同种子同分割
    assert a != c                       # 异种子异分割


# ---------------------------------------------------------------------------
# 回放可复现 + 先验协议
# ---------------------------------------------------------------------------

def test_same_seed_replay_bitwise_reproducible(small_corpus):
    records, gold = small_corpus
    mixed = _build_all(records, gold)["mixed"]
    r1 = bl.run_one(mixed, seed=42)
    r2 = bl.run_one(mixed, seed=42)
    assert r1.metrics_dict() == r2.metrics_dict()
    strip = lambda rows: json.dumps(   # noqa: E731  剥离墙钟计时字段
        [{k: v for k, v in row.items() if k != "step_elapsed_s"}
         for row in rows])
    assert strip(r1.trajectory) == strip(r2.trajectory)
    assert bl.assert_reproducible(mixed, seed=42).metrics_dict() == r1.metrics_dict()


def test_asreview_prior_protocol_shape(small_corpus):
    records, gold = small_corpus
    conds = _build_all(records, gold)
    r = bl.run_one(conds["mixed"], seed=42)
    assert r.trajectory[0]["label"] == "include"   # 先纳入
    assert r.trajectory[1]["label"] == "exclude"   # 后排除
    assert all(row["phase"] == "prior" for row in r.trajectory[:2])
    # 同种子跨条件抽到同一位置(先验位置由 keys 顺序决定 ⇒ 同一记录)
    r_en = bl.run_one(conds["en"], seed=42)
    assert ([row["record_id"] for row in r.trajectory[:2]]
            == [row["record_id"] for row in r_en.trajectory[:2]])


def test_relevant_positions_from_trajectory(small_corpus):
    records, gold = small_corpus
    en = _build_all(records, gold)["en"]
    r = bl.run_one(en, seed=42)
    pos = bl.relevant_positions(r)
    n_rel = sum(1 for v in gold.values() if v == "include")
    assert len(pos) == n_rel
    for row in r.trajectory:
        if row["label"] == "include":
            assert pos[row["record_id"]] == row["i"]


# ---------------------------------------------------------------------------
# 机翻确定性(同输入同输出)与缓存
# ---------------------------------------------------------------------------

def test_fake_translation_deterministic():
    for text in ("Randomized trial of estrogen therapy",
                 "骨密度 bone density 12 weeks",
                 "", "   "):
        assert bl.fake_en_zh(text) == bl.fake_en_zh(text)


def test_translation_cache_determinism_and_cleaning(tmp_path):
    calls: list[str] = []

    def counting_fn(text: str) -> str:
        calls.append(text)
        return "译文<" + text[:5] + ">"

    cache = bl.TranslationCache(tmp_path / "mt_cache.json", counting_fn)
    src = "<h2>Abstract</h2>  A randomized trial."
    out1 = cache(src)
    out2 = cache(src)                    # 第二次 = 缓存命中
    assert out1 == out2
    assert calls == [bl._clean_for_mt(src)]   # 只真正翻译一次,且输入已清洗
    cache.flush()
    cache2 = bl.TranslationCache(tmp_path / "mt_cache.json", counting_fn)
    assert cache2(src) == out1           # 落盘重载:同键同值,不重翻
    assert calls == [bl._clean_for_mt(src)]
    assert cache("") == ""               # 空文本直通(不进缓存)
    assert cache("   ") == "   "


# ---------------------------------------------------------------------------
# 机制量:词表 / 稀疏 / 静态与轨迹盲区
# ---------------------------------------------------------------------------

def test_corpus_stats_and_static_blindness():
    recs = [
        bl.FastRecord(zotero_key="A", title="alpha beta"),
        bl.FastRecord(zotero_key="B", title="randomized trial"),
        bl.FastRecord(zotero_key="C", title="随机对照试验"),
    ]
    counters = bl.doc_counters(recs)
    assert counters["A"] == {"alpha": 1, "beta": 1, "alpha_beta": 1}
    assert counters["C"] == {"随机": 1, "机对": 1, "对照": 1, "照试": 1, "试验": 1}
    st = bl.corpus_stats(counters)
    assert st["V"] == 3 + 3 + 5     # A: alpha/beta/alpha_beta;B 同型 3 个;C 5 个
    assert st["nnz_mean"] == pytest.approx(11 / 3)   # A=3, B=3, C=5
    blind = bl.static_blindness({"C": counters["C"]}, {"alpha", "beta"})
    assert blind["oov_mass_rate"] == pytest.approx(1.0)
    assert blind["n_blind"] == 1
    mixed_in = bl.static_blindness(counters, {"alpha", "beta", "randomized"})
    assert 0.0 < mixed_in["oov_mass_rate"] < 1.0


def test_trajectory_blindness_synthetic():
    counters = {
        "L1": {"tok": 2},                 # 已筛(标签)文档
        "U1": {"tok": 1, "other": 1},     # 剩余:OOV 质量 1/2
        "U2": {"zh1": 1, "zh2": 2},       # 剩余:全 OOV ⇒ 盲文档
    }
    traj = [{"record_id": "L1", "i": 1, "label": "include"}]
    out = bl.trajectory_blindness(counters, zh_keys=frozenset({"U2"}),
                                  trajectory=traj, checkpoint_every=1, warmup=1)
    assert len(out["checkpoints"]) == 1
    cp = out["checkpoints"][0]
    assert cp["vocab_labeled"] == 1                       # {"tok"}
    assert cp["oov_mass_rate"] == pytest.approx(4 / 5)    # (1 + 3) / (2 + 3)
    assert cp["oov_mass_rate_zh"] == pytest.approx(1.0)   # U2 全 OOV
    assert cp["oov_mass_rate_en"] == pytest.approx(0.5)   # U1 半 OOV
    assert cp["blind_frac"] == pytest.approx(1 / 2)
    assert out["mean_blind_frac_zh"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 端到端 mini 试点:真实 CSV(只读)+ 伪机翻 + 临时输出
# ---------------------------------------------------------------------------

def test_end_to_end_pilot_mini(tmp_path):
    res = bl.run_pilot(datasets=("Nelson_2002",), seeds=(1, 2),
                       out_dir=tmp_path, translate_fn=bl.fake_en_zh)
    assert len(res["rows"]) == 5 * 2                     # 5 条件 × 2 种子
    assert len(res["agg"]) == 5
    for f in ("pilot_runs.json", "pilot_summary.csv", "pilot_agg.csv",
              "mechanism_stats.json", "rel_positions_Nelson_2002.csv",
              "mt_cache.json"):
        assert (tmp_path / f).exists(), f
    runs = json.loads((tmp_path / "pilot_runs.json").read_text(encoding="utf-8"))
    assert runs["protocol"]["mt_split_seed"] == bl.MT_SPLIT_SEED
    # 同 (dataset, condition) 各种子 n_records/n_relevant 一致(标签不变)
    by_cond: dict = {}
    for row in runs["runs"]:
        by_cond.setdefault(row["condition"], []).append(row)
    for rows_ in by_cond.values():
        assert len({(r["n_records"], r["n_relevant"]) for r in rows_}) == 1
    mech = json.loads(
        (tmp_path / "mechanism_stats.json").read_text(encoding="utf-8"))
    assert mech["Nelson_2002"]["vocab"]["overlap_size"] >= 0
