"""2.1.5 W6 红钉：近邻聚类排序（第四类排序方式）——设计 §四 B5 全清单。

钉子与设计条目的对照：

| B5 条目 | 测试类 |
|---|---|
| 确定性（同数据 + 同 seed + 同已决集合 → 完全同序） | ``TopicOrderUnitNails`` |
| 已决冻结（筛过的位置不动） | ``TopicOrderUnitNails`` |
| 零向量（无标题无摘要）按原序排尾部 | ``TopicOrderUnitNails`` |
| 中英混合向量形状（复用 ML 同一 TfidfVectorizer） | ``TopicOrderUnitNails`` |
| **审计钉：排序策略不影响决定集合**（同一决定序列在 max 与 cluster 下一致） | ``AuditDecisionSetNails`` |
| 纯函数无 IO（零文件/零网络/零模型/零缓存） | ``NoIoNails`` |
| 性能钉：遍历段 <2s（真实形态 + 稠密语料），全量在真实形态 <2s、稠密语料设回归上限 | ``PerformanceNails`` |
| 精确并列由 base_order 稳定打破（裁决 B' 确定性加固） | ``TopicOrderUnitNails`` |
| 未配模型可选 cluster 且不报错 | ``ClusterHttpNails`` |
| 初筛/首页（/al/rank）、复筛（/stage2/queue、/work-queue/fulltext）接入 | ``ClusterHttpNails`` |

每条钉子都断言**修复后的行为**（撤掉 2.1.5 W6 的改动即红）：
- 撤掉 ``_AL_STRATEGIES`` 的 cluster 值 → PUT 设置 422、`/al/rank` 仍走模型分支；
- 撤掉 `/al/rank` 的 cluster 分支 → 0 标签时 `available=False` 且 `reason=not_enough_labels`；
- 撤掉 `work-queue/fulltext` 排序钩子 → 复筛队列恒为导入序；
- 撤掉 `_AL_STRATEGIES` 的 cluster 值但保留分支 → 设置无法保存（红）。

运行（定向，勿跑全量）::

    python -m pytest build/tests/test_2_1_5_w6_topic_cluster.py -q
"""
from __future__ import annotations

import builtins
import json
import os
import random
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与既有服务端夹具同法：先设环境再导入 main。
fixture = tempfile.TemporaryDirectory(prefix="rf_2_1_5_w6_cluster_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import fulltext as fulltext_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen import topic_order  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import main as main_mod  # noqa: E402
from custom_backend.main import app  # noqa: E402

TOPIC_TEXTS = {
    "rct": (
        "randomized controlled trial of statin therapy in heart failure",
        "patients with heart failure were randomized to statin therapy or "
        "placebo; cardiovascular outcomes and mortality were compared",
    ),
    "qual": (
        "qualitative interview study of nurse handover communication",
        "semi structured interviews with nurses about handover communication "
        "in hospital wards were analysed thematically",
    ),
    "ml": (
        "machine learning prediction model for readmission risk",
        "gradient boosting model predicted hospital readmission from "
        "electronic health records with cross validation",
    ),
}


def _article(key: str, topic: str | None) -> Article:
    """题录条目；``topic=None`` 表示无标题无摘要（零向量条目）。"""
    if topic is None:
        return Article(zotero_key=key)
    title, abstract = TOPIC_TEXTS[topic]
    return Article(zotero_key=key, title=f"{title} [{key}]", abstract=abstract)


class TopicOrderUnitNails(unittest.TestCase):
    """纯函数层：确定性、已决冻结、零向量尾部、混合向量形状、排列不变。"""

    def _vectors(self, mapping):
        return dict(zip(mapping, topic_order.vectorize(mapping.values())))

    def test_same_data_same_seed_same_decided_set_is_bit_identical(self):
        """同数据 + 同 seed + 同已决集合 → 完全同序（设计 §三.4）。"""
        keys = [f"K{i}" for i in range(24)]
        texts = [f"{TOPIC_TEXTS[t][0]} {i}" for i, t in
                 enumerate(["rct", "qual", "ml"] * 8)]
        vectors = dict(zip(keys, topic_order.vectorize(texts)))
        decided = {"K1", "K5", "K9"}
        first = topic_order.cluster_order(vectors, decided, 42, keys)
        for _ in range(3):
            self.assertEqual(
                first, topic_order.cluster_order(vectors, decided, 42, keys))
        # 同数据不同 seed：起点可能不同（允许），但仍是合法排列
        second = topic_order.cluster_order(vectors, decided, 7, keys)
        self.assertEqual(sorted(second), sorted(keys))

    def test_seed_controls_starting_point(self):
        """种子决定遍历起点：不同种子下首位可不同（可复现性由种子承担）。"""
        keys = [f"K{i}" for i in range(12)]
        texts = [f"{TOPIC_TEXTS['rct'][0]} variant {i}" for i in range(12)]
        vectors = dict(zip(keys, topic_order.vectorize(texts)))
        starts = {topic_order.cluster_order(vectors, set(), seed, keys)[0]
                  for seed in range(12)}
        self.assertGreater(len(starts), 1, "种子未参与起点选择")

    def test_exact_ties_are_broken_by_base_order(self):
        """量化后精确并列（余弦完全相同）由 base_order 先后决定（稳定键）。

        裁决 B' 第 3 条：相似度量化到 12 位小数后相等即视为并列，`argmax`
        取首个最大值 = `base_order` 里最靠前的未决条目；这里用 5 条**完全
        相同**的题录构造全并列（余弦恒为 1.0），断言遍历在起点之后严格按
        导入序推进，且换种子只换起点、不破坏该规则。
        """
        keys = ["K0", "K1", "K2", "K3", "K4"]
        identical = ("randomized trial statin heart failure outcomes",
                     "patients randomized to statin therapy or placebo")
        vectors = dict(zip(keys, topic_order.vectorize(
            [" ".join(identical)] * len(keys))))
        for seed in (0, 1, 7, 42, 2026):
            order = topic_order.cluster_order(vectors, set(), seed, keys)
            self.assertEqual(sorted(order), sorted(keys))
            self.assertEqual(order[1:], sorted(order[1:]),
                             f"并列未按 base_order 打破（seed={seed}）：{order}")

    def test_rowwise_memory_path_matches_dense_path(self):
        """超过稠密相似度矩阵上限时走逐行稀疏乘：排序结果与稠密路径一致。

        两条分支只是内存策略（O(m²) 一次性矩阵 vs O(m) 逐行），数学同一、
        换库规模不会换序——大库（>5000 条）与小库的行为必须可互相印证。
        """
        keys = [f"K{index}" for index in range(30)]
        topics = ["rct", "qual", "ml"] * 10
        vectors = dict(zip(keys, topic_order.vectorize(
            [f"{TOPIC_TEXTS[topic][0]} {topic} {index}"
             for index, topic in enumerate(topics)])))
        dense = topic_order.cluster_order(vectors, {"K2"}, 42, keys)
        original = topic_order._DENSE_SIMILARITY_LIMIT
        topic_order._DENSE_SIMILARITY_LIMIT = 0
        try:
            rowwise = topic_order.cluster_order(vectors, {"K2"}, 42, keys)
        finally:
            topic_order._DENSE_SIMILARITY_LIMIT = original
        self.assertEqual(rowwise, dense)

    def test_decided_keys_are_frozen_at_original_positions(self):
        """已决键冻结原位：筛过的位置不动，只重排未决段。"""
        keys = ["A", "B", "C", "D", "E", "F", "G", "H"]
        topics = ["ml", "rct", "qual", "rct", "ml", "qual", "rct", "ml"]
        vectors = dict(zip(keys, topic_order.vectorize(
            [f"{TOPIC_TEXTS[t][0]} {t}" for t in topics])))
        decided = {"B", "E", "H"}
        order = topic_order.cluster_order(vectors, decided, 42, keys)
        for key in decided:
            self.assertEqual(order.index(key), keys.index(key),
                             f"已决键 {key} 位置漂移：{order}")
        # 未决段仍是同一集合
        self.assertEqual({k for k in order if k not in decided},
                         {k for k in keys if k not in decided})

    def test_zero_vector_items_go_to_tail_in_original_order(self):
        """全缺文本 = 零向量：不参与遍历，按原相对顺序排在未决段尾部。"""
        keys = ["A", "Z1", "B", "Z2", "C", "Z3"]
        vectors = {
            "A": topic_order.vectorize([TOPIC_TEXTS["rct"][0]])[0],
            "B": topic_order.vectorize([TOPIC_TEXTS["qual"][0]])[0],
            "C": topic_order.vectorize([TOPIC_TEXTS["ml"][0]])[0],
            # Z1/Z2/Z3 不在 vectors 中 → 视作零向量
        }
        order = topic_order.cluster_order(vectors, set(), 42, keys)
        self.assertEqual(order[-3:], ["Z1", "Z2", "Z3"], order)
        # 全零向量（含显式空字典）：整段保持原序
        self.assertEqual(
            topic_order.cluster_order({"A": {}, "B": {}}, set(), 42, ["A", "B"]),
            ["A", "B"])
        self.assertEqual(topic_order.cluster_order({}, set(), 42, keys), keys)

    def test_output_is_always_a_permutation_including_duplicate_keys(self):
        keys = ["A", "A", "B", "C", "B"]
        vectors = {"A": topic_order.vectorize([TOPIC_TEXTS["rct"][0]])[0],
                   "B": topic_order.vectorize([TOPIC_TEXTS["ml"][0]])[0],
                   "C": topic_order.vectorize([TOPIC_TEXTS["qual"][0]])[0]}
        order = topic_order.cluster_order(vectors, {"A"}, 42, keys)
        self.assertEqual(sorted(order), sorted(keys))

    def test_mixed_chinese_english_vector_shape(self):
        """中英混合：复用 ML 同一 TfidfVectorizer —— 英文 unigram+bigram、
        中文二字组、L2 归一化；同文本两次向量化逐位相同。"""
        texts = [
            "deep learning 预测 model for 再入院风险",
            "randomized trial 随机对照试验 of statin 他汀",
        ]
        vectors = topic_order.vectorize(texts)
        for vector in vectors:
            self.assertIsInstance(vector, dict)
            self.assertAlmostEqual(
                sum(value * value for value in vector.values()), 1.0, places=12)
            terms = set(vector)
            self.assertTrue(any("_" in term for term in terms),  # 英文 bigram
                            f"缺少英文 bigram：{sorted(terms)[:10]}")
            self.assertTrue(any(len(t) == 2 and "\u4e00" <= t[0] <= "\u9fff"
                                for t in terms),  # 中文二字组
                            f"缺少中文二字组：{sorted(terms)[:10]}")
        self.assertIn("learning", vectors[0])
        self.assertIn("statin", vectors[1])
        self.assertIn("他汀", vectors[1])
        self.assertEqual(vectors, topic_order.vectorize(texts))
        # 零向量 / 非字符串输入
        self.assertEqual(topic_order.vectorize(["", None, "the and of"]), [{}, {}, {}])

    def test_topic_text_joins_title_abstract_and_keywords(self):
        self.assertEqual(topic_order.topic_text("A", "B", "C"), "A B C")
        self.assertEqual(topic_order.topic_text("A", "", None), "A")
        self.assertEqual(topic_order.topic_text(None, None, None), "")

    def test_similar_documents_end_up_adjacent(self):
        """同主题成段：三组强分离文本从交错基准序重排后组内相邻。"""
        keys = ["R1", "Q1", "M1", "R2", "Q2", "M2", "R3", "Q3", "M3"]
        topics = ["rct", "qual", "ml"] * 3
        texts = [f"{TOPIC_TEXTS[t][0]} {TOPIC_TEXTS[t][1]}" for t in topics]
        vectors = dict(zip(keys, topic_order.vectorize(texts)))
        order = topic_order.cluster_order(vectors, set(), 42, keys)
        for prefix in ("R", "Q", "M"):
            positions = [i for i, key in enumerate(order) if key.startswith(prefix)]
            self.assertEqual(positions,
                             list(range(min(positions), min(positions) + 3)),
                             f"{prefix} 组未成段：{order}")


class NoIoNails(unittest.TestCase):
    """纯函数无 IO：零文件、零网络、零模型文件、零缓存。"""

    def test_runtime_does_no_file_or_socket_io(self):
        keys = [f"K{i}" for i in range(6)]
        texts = [TOPIC_TEXTS[t][1] for t in ("rct", "qual", "ml") * 2]
        boom = AssertionError("topic_order 不得做文件/网络 IO")

        def _forbidden(*args, **kwargs):
            raise boom

        with mock.patch.object(builtins, "open", _forbidden), \
                mock.patch.object(socket, "socket", _forbidden), \
                mock.patch.object(socket, "create_connection", _forbidden):
            vectors = dict(zip(keys, topic_order.vectorize(texts)))
            order = topic_order.cluster_order(vectors, set(), 42, keys)
            again = topic_order.cluster_order(vectors, set(), 42, keys)
        self.assertEqual(order, again)

    def test_module_source_declares_no_io_or_model_dependency(self):
        """零 IO / 零联网 / 零模型文件；数值内核=numpy+scipy（既有运行时依赖）。

        2026-11-08 裁决 B'：相似度与遍历只走 numpy/scipy（不保留纯标准库
        兜底，避免两条路径浮点次序不同），numpy/scipy 版本已钉在
        build/requirements-app.txt。设计 §三.1 禁的是“新组件/模型文件/
        新第三方依赖/联网”，既有依赖不违约。
        """
        source = Path(topic_order.__file__).read_text(encoding="utf-8")
        for forbidden in ("import os", "import io", "import socket", "urllib",
                          "requests", "pickle", "sqlite3", "joblib",
                          "sklearn", "open(", "Path(", "json"):
            self.assertNotIn(forbidden, source,
                             f"topic_order.py 出现不允许的依赖/IO：{forbidden}")
        # 数值内核与向量化来源都是显式、单一路径（无 ImportError 降级分支）
        self.assertIn("import numpy as np", source)
        self.assertIn("from scipy.sparse import csr_matrix", source)
        self.assertIn("from coscreen.al.features import TfidfVectorizer", source)
        self.assertNotIn("except ImportError", source)


class PerformanceNails(unittest.TestCase):
    """性能钉（2026-11-08 用户裁决 B' 的分段口径）。

    ① **遍历段**（相似度 + 贪心遍历）在「真实形态语料」与「稠密语料」
       （1784 条 × 250 词摘要，平均 ≈415 词元/条，ERIC/WoS 完整摘要量级）
       上都 < 2s——这是本泳道新增的 O(n²) 计算面；
    ② **全量流程**（含既有 ``TfidfVectorizer`` 向量化）在真实形态语料上 < 2s；
    ③ 稠密语料的**全量**只设回归上限（本机实测 × 1.5，见
       ``DENSE_FULL_BASELINE``）并打印实测——向量化耗时归既有
       ``features.py``（2.1.5 之前 ML 排序每次重排付的是同一笔钱），
       不是本泳道能优化的面，故不按 2s 硬钉。

    语料均为确定性合成（零网络取样）：
    - 真实形态：1784 条，60% 带 60–150 词摘要、40% 仅标题；80 个主题词池 ×
      40 词；摘要共享 5 个“常用词”；其余长尾（常用词 df≈200/1784≈11%）。
    - 稠密：1784 条 × 250 词摘要，3 万词 Zipf 词表，80 个主题池（≈415 词元/条）。

    探针与逐档实测：``E:/rf214tmp/g3/benchproto10.py`` / ``w6_perf_probe.txt``。
    """

    RECORDS = 1784
    #: 稠密语料全量回归基线：本机 2026-11-08 实测 4.31–5.24s（向量化 3.92–4.61s
    #: + 遍历 0.38–0.63s），取实测上界 ×1.5 ≈ 7.9s 作为回归上限并打印实测。
    DENSE_FULL_BASELINE = 5.24

    @staticmethod
    def _realistic_corpus(n=RECORDS, seed=20261108):
        rng = random.Random(seed)
        syllables = ["al", "be", "cor", "di", "en", "fo", "ga", "hi", "in",
                     "jo", "ka", "lu", "ma", "no", "or", "pe", "qua", "ra",
                     "sa", "tu", "un", "vi", "wa", "xe", "yo", "za", "tri"]

        def word():
            return "".join(rng.choice(syllables) for _ in range(rng.randint(2, 4)))

        common = [word() for _ in range(30)]
        topics = [[word() for _ in range(40)] for _ in range(80)]
        tail = list(dict.fromkeys(word() for _ in range(40000)))
        documents = []
        for _ in range(n):
            topic = topics[rng.randrange(len(topics))]
            title = " ".join(
                rng.choice(topic) if rng.random() < 0.5 else rng.choice(tail)
                for _ in range(9))
            parts = [title]
            if rng.random() < 0.6:
                anchor = rng.sample(common, 5)
                body = []
                for index in range(rng.randint(60, 150)):
                    pick = rng.random()
                    if pick < 0.45:
                        body.append(rng.choice(topic))
                    elif pick < 0.52:
                        body.append(anchor[index % 5])
                    else:
                        body.append(rng.choice(tail))
                parts.append(" ".join(body))
            documents.append(" ".join(parts))
        return documents

    @staticmethod
    def _dense_corpus(n=RECORDS, seed=424242, words=250):
        rng = random.Random(seed)
        syllables = ["al", "be", "cor", "di", "en", "fo", "ga", "hi", "in",
                     "jo", "ka", "lu", "ma", "no", "or", "pe", "qua", "ra",
                     "sa", "tu"]
        vocabulary = list(dict.fromkeys(
            "".join(rng.choice(syllables) for _ in range(rng.randint(2, 4)))
            for _ in range(30000)))
        weights = [1.0 / (index + 1) ** 1.05 for index in range(len(vocabulary))]
        topics = [rng.sample(vocabulary[:6000], 60) for _ in range(80)]
        documents = []
        for _ in range(n):
            topic = topics[rng.randrange(len(topics))]
            title = " ".join(rng.choices(vocabulary[:8000],
                                         weights=weights[:8000], k=10))
            body = [rng.choice(topic) if rng.random() < 0.45
                    else rng.choices(vocabulary, weights=weights, k=1)[0]
                    for _ in range(words)]
            documents.append(title + " " + " ".join(body))
        return documents

    def test_01_realistic_corpus_full_rerank_under_two_seconds(self):
        documents = self._realistic_corpus()
        keys = [f"K{index:04d}" for index in range(len(documents))]
        started = time.perf_counter()
        vectors = dict(zip(keys, topic_order.vectorize(documents)))
        vectorized = time.perf_counter()
        order = topic_order.cluster_order(vectors, set(), 42, keys)
        finished = time.perf_counter()
        vectorize_seconds = vectorized - started
        traversal_seconds = finished - vectorized
        print(f"\n[W6 perf] 真实形态 1784 条：向量化 {vectorize_seconds:.2f}s + "
              f"遍历 {traversal_seconds:.2f}s = {finished - started:.2f}s")
        self.assertEqual(sorted(order), sorted(keys))
        self.assertLess(traversal_seconds, 2.0,
                        f"遍历段 {traversal_seconds:.2f}s（要求 < 2s）")
        self.assertLess(finished - started, 2.0,
                        f"真实形态全量 {finished - started:.2f}s（要求 < 2s）")

    def test_02_dense_corpus_traversal_under_two_seconds_full_ceiling(self):
        documents = self._dense_corpus()
        keys = [f"K{index:04d}" for index in range(len(documents))]
        started = time.perf_counter()
        vectors = dict(zip(keys, topic_order.vectorize(documents)))
        vectorized = time.perf_counter()
        order = topic_order.cluster_order(vectors, set(), 42, keys)
        finished = time.perf_counter()
        vectorize_seconds = vectorized - started
        traversal_seconds = finished - vectorized
        full_seconds = finished - started
        tokens_per_record = sum(len(v) for v in vectors.values()) / len(vectors)
        print(f"\n[W6 perf] 稠密 1784 条（{tokens_per_record:.0f} 词元/条）："
              f"向量化 {vectorize_seconds:.2f}s + 遍历 {traversal_seconds:.2f}s "
              f"= {full_seconds:.2f}s（全量回归上限 "
              f"{self.DENSE_FULL_BASELINE * 1.5:.2f}s）")
        self.assertEqual(sorted(order), sorted(keys))
        self.assertLess(traversal_seconds, 2.0,
                        f"稠密语料遍历段 {traversal_seconds:.2f}s（要求 < 2s）")
        # 全量 = 既有向量化 + 本次遍历；只设回归上限（本机实测 × 1.5）。
        self.assertLess(
            full_seconds, self.DENSE_FULL_BASELINE * 1.5,
            f"稠密语料全量 {full_seconds:.2f}s 超出回归上限 "
            f"{self.DENSE_FULL_BASELINE * 1.5:.2f}s")


class ClusterHttpNails(unittest.TestCase):
    """HTTP 层：设置枚举、无模型可用、队列接入、载荷形状。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        try:
            user = auth_mod.create_user("w6cluster@example.test",
                                        "W6-Cluster-Test-2026")
        except auth_mod.AuthError:
            user = auth_mod.get_user("w6cluster@example.test")
        cls.username = user["username"]
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}
        cls.created_tasks: list[str] = []

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.task = None

    # ---- 夹具 ----
    def _new_task(self, name: str) -> str:
        response = self.client.post("/api/tasks", json={"name": name},
                                    headers=self.auth)
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["task_id"]
        self.created_tasks.append(task_id)
        self.task = task_id
        return task_id

    def _db(self):
        info = tasks_mod.find_task(self.task, str(main_mod.DATA_DIR))
        return main_mod._screener_db(info, self.username)

    def _seed(self, articles, decisions=None, stage2=None):
        db = self._db()
        db_mod.upsert_articles(db, articles, [])
        for key, decision in (decisions or {}).items():
            db_mod.save_decision(db, key, decision, "", "", [])
        for key, decision in (stage2 or {}).items():
            fulltext_mod.save_stage2(db, key, decision)

    def _api(self, path):
        return self.client.get(f"/api/tasks/{self.task}{path}", headers=self.auth)

    def _put(self, path, body):
        return self.client.put(f"/api/tasks/{self.task}{path}", json=body,
                               headers=self.auth)

    def _post(self, path, body):
        return self.client.post(f"/api/tasks/{self.task}{path}", json=body,
                                headers=self.auth)

    def _use_cluster(self):
        response = self._put("/al/settings", {"strategy": "cluster"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["strategy"], "cluster")

    # ---- 设置 ----
    def test_settings_accept_cluster_and_keep_old_values_working(self):
        """排序策略枚举加值；旧值（max/uncertainty/mixed）行为不变。"""
        self._new_task("w6-settings")
        self._use_cluster()
        self.assertEqual(self._api("/al/settings").json()["strategy"], "cluster")
        for legacy in ("max", "mixed", "uncertainty"):
            response = self._put("/al/settings", {"strategy": legacy})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["strategy"], legacy)
        # 老库无迁移：设置按“任务 + 筛选员”存 JSON，未提供的字段保留原值
        # （这里只改 seed，strategy 仍是上一次显式保存的 uncertainty）。
        response = self._put("/al/settings", {"seed": 7})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["strategy"], "uncertainty")
        self.assertEqual(payload["seed"], 7)
        # 非法枚举值仍按既有校验形状拒绝（422）
        for bad in ("Cluster", "topic-cluster", ""):
            refused = self._put("/al/settings", {"strategy": bad})
            self.assertEqual(refused.status_code, 422, (bad, refused.text))

    # ---- 未配模型可选 cluster 且不报错 ----
    def test_cluster_rank_works_without_model_or_labels(self):
        """0 标签、未配任何模型：cluster 仍 available=True 且不构造排序器。"""
        self._new_task("w6-no-model")
        self._seed([_article(f"K{i}", t) for i, t in enumerate(
            ("rct", "qual", "ml", "rct", "qual", "ml"))])
        self._use_cluster()
        with mock.patch.object(main_mod, "ActiveLearningRanker",
                               side_effect=AssertionError("cluster 不得训练模型")):
            response = self._api("/al/rank")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIs(payload["available"], True)
        self.assertNotIn("reason", payload)
        self.assertEqual(payload["strategy"], "cluster")
        self.assertEqual(payload["seed"], 42)
        self.assertEqual(payload["n_labeled"], 0)
        self.assertEqual(sorted(item["zotero_key"] for item in payload["order"]),
                         [f"K{i}" for i in range(6)])
        # 聚类不产生“纳入概率”：score 恒为 null（不臆造分数）
        self.assertTrue(all(item["score"] is None for item in payload["order"]))
        # 状态端点同样“可用”
        status = self._api("/al/status").json()
        self.assertIs(status["available"], True)
        self.assertEqual(status["strategy"], "cluster")

    def test_cluster_rank_empty_queue_is_clean_terminal_state(self):
        """无任何题录（或全部已决）：200 + 空序，不是错误。"""
        self._new_task("w6-empty")
        self._use_cluster()
        payload = self._api("/al/rank").json()
        self.assertIs(payload["available"], True)
        self.assertEqual(payload["order"], [])
        self.assertIsInstance(payload["stop_suggestion"], dict)
        self.assertEqual(self._api("/stage2/queue").json(), {"queue": [], "total": 0})

    def test_cluster_rank_excludes_decided_and_is_deterministic(self):
        self._new_task("w6-candidates")
        self._seed(
            [_article(f"K{i}", t) for i, t in enumerate(
                ("rct", "qual", "ml", "rct", "qual", "ml"))],
            decisions={"K0": "include", "K3": "exclude", "K2": "include"})
        self._use_cluster()
        first = self._api("/al/rank").json()
        second = self._api("/al/rank").json()
        self.assertEqual(
            [item["zotero_key"] for item in first["order"]],
            [item["zotero_key"] for item in second["order"]])
        self.assertEqual(
            sorted(item["zotero_key"] for item in first["order"]),
            ["K1", "K4", "K5"], "已决条目不得回到待筛队列")

    # ---- 复筛队列（/stage2/queue）----
    def test_stage2_queue_cluster_reorders_but_freezes_decided(self):
        self._new_task("w6-stage2-queue")
        topics = ["rct", "qual", "ml"] * 3
        self._seed([_article(f"S{i}", topic) for i, topic in enumerate(topics)],
                   decisions={f"S{i}": "include" for i in range(9)},
                   stage2={"S1": "include", "S7": "exclude"})
        baseline = [item["zotero_key"] for item in self._api("/stage2/queue").json()["queue"]]
        self._use_cluster()
        payload = self._api("/stage2/queue").json()
        keys = [item["zotero_key"] for item in payload["queue"]]
        self.assertEqual(payload["total"], len(keys))
        self.assertEqual(sorted(keys), sorted(baseline))
        # 已决条目冻结在导入序原位
        for key in ("S1", "S7"):
            self.assertEqual(keys.index(key), baseline.index(key), keys)
        self.assertNotEqual(keys, baseline, "cluster 未生效：复筛队列仍是导入序")

    # ---- 初筛/首页（/al/rank）与复筛工作队列（/work-queue/fulltext）----
    def test_work_queue_fulltext_clusters_while_screen_keeps_import_order(self):
        self._new_task("w6-work-queue")
        topics = ["rct", "qual", "ml"] * 3
        self._seed([_article(f"W{i}", topic) for i, topic in enumerate(topics)],
                   decisions={f"W{i}": "include" for i in range(9)})
        screen_before = [row["zotero_key"] for row in
                         self._api("/work-queue/screen").json()["queue"]]
        fulltext_before = [row["zotero_key"] for row in
                           self._api("/work-queue/fulltext").json()["queue"]]
        self._use_cluster()
        screen_after = [row["zotero_key"] for row in
                        self._api("/work-queue/screen").json()["queue"]]
        fulltext_after = [row["zotero_key"] for row in
                          self._api("/work-queue/fulltext").json()["queue"]]
        # 初筛队列顺序由「导入序 + 前端按 /al/rank 合并」决定，服务端不得改动
        self.assertEqual(screen_after, screen_before)
        # 复筛队列按同一策略重排（只换顺序，条目不增不减）
        self.assertEqual(sorted(fulltext_after), sorted(fulltext_before))
        self.assertNotEqual(fulltext_after, fulltext_before)
        # 换回既有策略：复筛队列恢复导入序（既有行为不变）
        self._put("/al/settings", {"strategy": "mixed"})
        restored = [row["zotero_key"] for row in
                    self._api("/work-queue/fulltext").json()["queue"]]
        self.assertEqual(restored, fulltext_before)

    def test_work_queue_fulltext_freezes_stage2_decided_keys(self):
        self._new_task("w6-work-queue-frozen")
        topics = ["rct", "qual", "ml"] * 3
        self._seed([_article(f"F{i}", topic) for i, topic in enumerate(topics)],
                   decisions={f"F{i}": "include" for i in range(9)},
                   stage2={"F2": "include", "F6": "exclude"})
        baseline = [row["zotero_key"] for row in
                    self._api("/work-queue/fulltext").json()["queue"]]
        self._use_cluster()
        keys = [row["zotero_key"] for row in
                self._api("/work-queue/fulltext").json()["queue"]]
        for key in ("F2", "F6"):
            self.assertEqual(keys.index(key), baseline.index(key), keys)


class AuditDecisionSetNails(unittest.TestCase):
    """审计钉：排序策略只改阅读顺序，绝不影响决定集合及其记录/统计。

    做法与“既有决定回归套件”同口径：同一套逐条决定规则（按题录内容判定）
    分别在 **max 队列**、**cluster 队列**与导入序队列三条臂上执行，经**真实**
    ``POST /decisions`` 落库后比对最终纳入/排除集合（max 臂先落 5 条真实标签
    让模型训练，确保它走的是真 max 序而不是“标签不足→导入序”的退化形态）；
    并断言 ``/al/rank`` 本身不写任何决定。

    既有决定流套件在同一批定向回归里跑通（本文件之外）：
    ``test_2_1_5_w1_binary.py``（初筛/复筛决定与队列）、
    ``test_r12_reconciler_screening_toggle.py``（决定权限与筛选开关）。
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        try:
            user = auth_mod.create_user("w6audit@example.test",
                                        "W6-Audit-Test-2026")
        except auth_mod.AuthError:
            user = auth_mod.get_user("w6audit@example.test")
        cls.username = user["username"]
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    ARTICLES = tuple(
        _article(f"A{i:02d}", topic)
        for i, topic in enumerate(
            ["rct", "qual", "ml", "rct", "qual", "ml", "rct", "qual",
             "ml", "rct", "qual", "ml"]))

    @staticmethod
    def _rule(article) -> str:
        """决定规则只看题录内容（与顺序无关）：含 statin → 纳入，否则排除。"""
        return "include" if "statin" in article.title else "exclude"

    def _task(self, name):
        response = self.client.post("/api/tasks", json={"name": name},
                                    headers=self.auth)
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["task_id"]
        info = tasks_mod.find_task(task_id, str(main_mod.DATA_DIR))
        db = main_mod._screener_db(info, self.username)
        db_mod.upsert_articles(db, list(self.ARTICLES), [])
        return task_id, db

    def _decide_through(self, task_id, order):
        by_key = {article.zotero_key: article for article in self.ARTICLES}
        for key in order:
            response = self.client.post(
                f"/api/tasks/{task_id}/decisions",
                json={"zotero_key": key, "decision": self._rule(by_key[key])},
                headers=self.auth)
            self.assertEqual(response.status_code, 200, response.text)

    def _final_sets(self, db):
        return {row["decision"]: {r["zotero_key"] for r in db_mod.list_decisions(db)
                                  if r["decision"] == row["decision"]}
                for row in db_mod.list_decisions(db)}

    def test_same_decisions_same_final_sets_under_max_cluster_and_import(self):
        """同一决定序列在 **max 与 cluster** 下最终纳入/排除集合一致（B5 审计钉）。

        三条臂：
        - **cluster**：0 标签、策略 cluster，队列 = /al/rank 的聚类序；
        - **max**：先落 5 条与规则一致的历史决定（2 include / 3 exclude，满足
          min_labeled=5）让模型真正训练，队列 = /al/rank 的 max 序（剩余 7 条）；
        - **import**：不排序，按导入序逐条决定（既有队列的基准形态）。

        三臂喂的是**同一套逐条决定规则**（按题录内容判定，见 :meth:`_rule`），
        因此最终 (key -> decision) 映射必须逐键相同——排序只改阅读顺序，
        绝不改决定集合与统计。全部经真实 ``POST /decisions`` 落库后比对。
        """
        cluster_task, cluster_db = self._task("w6-audit-cluster")
        max_task, max_db = self._task("w6-audit-max")
        import_task, import_db = self._task("w6-audit-import")
        self.assertEqual(
            self.client.put(f"/api/tasks/{cluster_task}/al/settings",
                            json={"strategy": "cluster"}, headers=self.auth
                            ).status_code, 200)
        self.assertEqual(
            self.client.put(f"/api/tasks/{max_task}/al/settings",
                            json={"strategy": "max"}, headers=self.auth
                            ).status_code, 200)

        by_key = {article.zotero_key: article for article in self.ARTICLES}
        import_order = [article.zotero_key for article in self.ARTICLES]
        cluster_order = [item["zotero_key"] for item in self.client.get(
            f"/api/tasks/{cluster_task}/al/rank", headers=self.auth
        ).json()["order"]]
        self.assertNotEqual(cluster_order, import_order,
                            "审计前提不成立：聚类序与导入序必须不同")
        self._decide_through(cluster_task, cluster_order)

        # max 臂：先落 5 条与规则一致的历史决定（模型需要两类标签才能训练）
        seeded = {key: self._rule(by_key[key]) for key in import_order[:5]}
        self.assertEqual(sorted(seeded.values()).count("include"), 2, seeded)
        self.assertEqual(sorted(seeded.values()).count("exclude"), 3, seeded)
        self._decide_through(max_task, list(seeded))
        max_rank = self.client.get(f"/api/tasks/{max_task}/al/rank",
                                   headers=self.auth).json()
        self.assertIs(max_rank["available"], True, max_rank)
        self.assertEqual(max_rank["strategy"], "max")
        max_order = [item["zotero_key"] for item in max_rank["order"]]
        self.assertEqual(sorted(max_order), sorted(import_order[5:]),
                         "max 序必须恰好覆盖剩余待筛条目")
        self._decide_through(max_task, max_order)

        self._decide_through(import_task, import_order)

        final = {
            "cluster": self._final_sets(cluster_db),
            "max": self._final_sets(max_db),
            "import": self._final_sets(import_db),
        }
        self.assertEqual(final["cluster"], final["max"],
                         "cluster 与 max 的最终决定集合不一致")
        self.assertEqual(final["cluster"], final["import"],
                         "cluster 与导入序的最终决定集合不一致")
        included = {row["zotero_key"] for row in db_mod.list_decisions(cluster_db)
                    if row["decision"] == "include"}
        self.assertEqual(included, {"A00", "A03", "A06", "A09"})
        print(f"\n[W6 audit] cluster={cluster_order}\n[W6 audit] max(order)="
              f"{max_order}")

    def test_rank_endpoint_never_writes_decisions(self):
        task_id, db = self._task("w6-audit-no-write")
        self.client.put(f"/api/tasks/{task_id}/al/settings",
                        json={"strategy": "cluster"}, headers=self.auth)
        before = db_mod.list_decisions(db)
        for _ in range(2):
            response = self.client.get(f"/api/tasks/{task_id}/al/rank",
                                       headers=self.auth)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(db_mod.list_decisions(db), before)
        self.assertEqual(before, [])


class QueueOrderGuardPins(unittest.TestCase):
    """终审 #29：queue() 层钩子排列校验（第二道防线）的红钉。

    生产钩子 ``_cluster_queue_order`` 自带同型校验且有纯函数钉
    （TopicOrderUnitNails / main.py:3410-3415）；本类钉 queue() 这一层：
    注入「丢尾键 / 重复键 / 幽灵键 / None」钩子，``/work-queue/fulltext``
    必须仍返回与基线完全相同的完整键集（守卫丢弃坏序、保持基序，total 与
    条目数不矛盾）。删掉排列校验后 drop-last 会静默丢条目、duplicate 出
    重复键、extra-key 直接 500（终审探针 probe_mutation_run / probe_bad_hook
    / probe_mut_drop 已证），变异钉以「校验被中性化」的运行期等价复现。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        try:
            user = auth_mod.create_user("w6guard@example.test",
                                        "W6-Guard-Test-2026")
        except auth_mod.AuthError:
            user = auth_mod.get_user("w6guard@example.test")
        cls.username = user["username"]
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}
        cls.created_tasks: list[str] = []

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _new_task(self, name: str) -> str:
        response = self.client.post("/api/tasks", json={"name": name},
                                    headers=self.auth)
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["task_id"]
        self.created_tasks.append(task_id)
        self.task = task_id
        return task_id

    def _seed(self) -> None:
        self._new_task(self._testMethodName)
        info = tasks_mod.find_task(self.task, str(main_mod.DATA_DIR))
        db = main_mod._screener_db(info, self.username)
        db_mod.upsert_articles(
            db, [_article(f"G{i}", topic) for i, topic in
                 enumerate(["rct", "qual", "ml"] * 3)], [])
        for i in range(9):
            db_mod.save_decision(db, f"G{i}", "include", "", "", [])

    def _queue(self):
        response = self.client.get(
            f"/api/tasks/{self.task}/work-queue/fulltext", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_bad_hooks_cannot_drop_duplicate_or_add_keys(self):
        """四种坏钩子（丢尾/重复/幽灵/None）下队列键集与基线逐一相同。"""
        self._seed()
        baseline_keys = [row["zotero_key"] for row in self._queue()["queue"]]
        self.assertEqual(len(baseline_keys), 9, baseline_keys)

        runtime = app.state.review_workflow
        self.assertIsNotNone(runtime.queue_order, "前提：生产钩子已接线")
        bad_hooks = {
            "drop_last": lambda ctx, stage, keys: keys[:-1],
            "duplicate": lambda ctx, stage, keys: keys + [keys[0]],
            "extra_key": lambda ctx, stage, keys: keys + ["GHOST"],
            "none": lambda ctx, stage, keys: None,
        }
        for name, hook in bad_hooks.items():
            with self.subTest(hook=name):
                with mock.patch.object(runtime, "queue_order", hook):
                    payload = self._queue()
                keys = [row["zotero_key"] for row in payload["queue"]]
                self.assertEqual(keys, baseline_keys,
                                 f"{name} 钩子必须被守卫丢弃、保持基序")
                self.assertEqual(payload["total"], len(baseline_keys))

    def test_mutation_permutation_check_neutered_pin_goes_red(self):
        """破坏性变异：排列校验的两道比较被中性化（值保真的「恒等」len /
        sorted——模块内唯一的 len(...)==/sorted(...)== 比较就是守卫本体的
        两道，等效删除守卫）→ drop-last 钩子照单全收，队列静默丢一条——
        上一颗钉必红。"""
        import builtins as _builtins
        from custom_backend import review_workflow_api

        real_len, real_sorted = _builtins.len, _builtins.sorted

        class _EqAnyInt(int):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        class _EqAnyList(list):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        self._seed()
        baseline_keys = [row["zotero_key"] for row in self._queue()["queue"]]
        runtime = app.state.review_workflow
        with mock.patch.object(runtime, "queue_order",
                               lambda ctx, stage, keys: keys[:-1]), \
                mock.patch.object(review_workflow_api, "len",
                                  lambda seq: _EqAnyInt(real_len(seq)),
                                  create=True), \
                mock.patch.object(review_workflow_api, "sorted",
                                  lambda seq: _EqAnyList(real_sorted(seq)),
                                  create=True):
            payload = self._queue()
        keys = [row["zotero_key"] for row in payload["queue"]]
        self.assertEqual(keys, baseline_keys[:-1], keys)
        self.assertEqual(len(keys), 8,
                         "守卫被中性化时队列丢条目（= 排列校验钉必红）")


if __name__ == "__main__":
    unittest.main()
