"""r7-lastfix SVM 载体钉子测试（F-04-02 / F-04-03，专属新文件，撤修复必红）。

证据基线：glm_verify_v1.md §4/§5（两条均 confirmed，含复现命令与输出）
与第 1 轮评审复核意见（证据文件在主仓库 E:/mubi-reviewflow/out/
external_reviews/，本工作树 out/ 不含副本——按文件名+章节号索引）。

- F-04-02（契约违约）：svm/cjk 预设 + 候选为空（全筛完的合法终态，
  ``ranker.rank`` docstring 承诺 ``order == []``、``scores == {}``）修前
  被 ``svm._prepare`` 误报为「形态混用」``ValueError`` → ``GET
  /al/rank`` 500。第 1 轮修在 svm 载体（零样本预测短路）；第 2 轮评审
  实测残留：空候选 + 单类训练标签（全筛完且 5 条全 include/exclude）
  时 svm/cjk 仍在 fit 处抛单类 ValueError。第 3 轮口径：ranker.rank 的
  空候选短路**只对 svm 载体（svm/cjk）生效**（终态先于训练，
  ``n_features=0`` + ``trained=False``——未训练≠无特征）；NB 载体
  （tuned/legacy）与 ``profile=None`` 沿用原有代码路径（§10 不变量 1：
  含空候选下的 ``model_meta`` 与旧版 ebd78f3 逐位一致，本组差分探针
  fixed-vs-baseline 0 diff 实证）。
- F-04-03（单类训练崩溃）：svm/cjk 预设 + 训练标签单类（首批全
  include，现实可达）**且确有待排候选**时，``svm.fit`` 单类守卫先于
  sklearn 抛可读 :class:`~coscreen.al.svm.SingleClassTrainingError`
  （``ValueError`` 子类，含 include/exclude 计数与用户动作指引）。
  对照语义：NB 路径不抛——缺失类先验取 ``-inf``、单类降级为常数分
  1.0（``coscreen/al/nb.py`` 显式设计），本文件把该对照一并钉死。
  端点侧（第 2 轮经授权落位、第 3 轮按评审意见改形，custom_backend/
  main.py 仅三处：顶层 import 带 ``except ImportError`` 兜底（svm.py
  被回退时 app 仍可导入，红证以干净 failed/200 形态复现而非收集
  error）+ ``al_rank``/``get_shared_ranking`` 两处 ``except ValueError``
  内 ``isinstance`` 精确匹配该专用类型 → ``_bad(str(exc))`` 转 400）：
  单类形态 4xx + 可读文案，绝不 500；不吞 rank 路径其他 ValueError。
- 状态一致性（第 2 轮低危项）：失败的 dict fit（如非法
  sample_weight）不得把 ``_dict_mode`` 置 True——否则零样本短路会在
  未 fit 的模型上静默返回 ``[]``（假成功）。fit 的形态状态在
  ``_model.fit`` 成功后才提交；失败后空预测必须仍报错（sklearn
  失败 fit 可能已写部分内部状态，错误类型依版本为 ValueError，
  契约是「必须报错，绝不静默返回 []」）。

撤修复 → 变红映射（三层修复各自有钉子，对照组不陪跑）：
- 回退 ranker.py（去 svm 空候选短路）→ 实测 **7 failed**：F-04-02 的
  6 个 svm/cjk 空终态子测试（3 场景 × svm/cjk）+ HTTP 全筛完+全
  include 钉；tuned/legacy/None 子测试与两个 F-04-03 HTTP 钉保持绿
  （对照路径与守卫/转换层不受 ranker 回退影响——红证不自指）。
- 回退 svm.py → 实测 **9 failed**（0 error）：F-04-03 模块/载体钉
  （可读文案 + 专用异常类型）、载体级零样本预测、失败-fit 状态钉、
  两个 HTTP 单类钉（干净 failed，500）；F-04-02 的 ranker 层钉与
  HTTP 空候选钉保持绿（200）。
- 回退 main.py 三处转换（定向反编辑，不动兄弟组改动）→ 实测两个
  HTTP 单类钉必红（500），其余全绿。
- R2 护栏（防新增严格性，非钉子）：未 fit 的空输入仍 sklearn
  ``NotFittedError``；numpy 数组 ``y`` 不被守卫拦截（保持 sklearn
  直调等价，原生文案照旧）；数组模式空列表预测仍透传 sklearn 校验。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_svm_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen.al.ranker import ActiveLearningRanker
from coscreen.al.svm import SVMClassifier

try:  # 专用异常类型是本组修复的一部分；撤修复（回基线）时按缺失变红，
    # 而不是让整文件收集失败（那会掩蔽其余钉子的逐条红证）。
    from coscreen.al.svm import SingleClassTrainingError
except ImportError:  # pragma: no cover - 仅撤修复时可达
    SingleClassTrainingError = None
from coscreen.models import Article

#: 全部四个预设（svm 载体两种 + NB 载体两种对照）
_PROFILES = ("svm", "cjk", "tuned", "legacy")
_SVM_PROFILES = ("svm", "cjk")
_NB_PROFILES = ("tuned", "legacy")

#: F-04-02 语料：5 篇、3 include + 2 exclude、全部筛完 → 候选为空。
_ARTICLES_2CLASS = [
    Article(
        zotero_key=f"A{i}",
        title=f"trial {i} beta blocker heart failure",
        abstract=f"randomized controlled cohort {i} mortality",
        authors="Au A", journal="J",
    )
    for i in range(1, 6)
]
_DECISIONS_FULL = {f"A{i}": ("include" if i % 2 else "exclude") for i in range(1, 6)}

#: 第 2 轮补：空候选 + 单类训练标签（全筛完且 5 条全 include / 全
#: exclude）——第 1 轮评审实测 svm/cjk 在 fit 处抛单类 ValueError 的
#: 残留场景；空终态必须优先于训练，四预设一致。
_DECISIONS_ALL_INCLUDE = {f"A{i}": "include" for i in range(1, 6)}
_DECISIONS_ALL_EXCLUDE = {f"A{i}": "exclude" for i in range(1, 6)}

#: F-04-03 语料：8 篇、前 5 篇全 include（≥ 默认 min_labeled=5）、
#: B6-B8 未筛 → 单类训练 + 3 条候选。
_ARTICLES_1CLASS = [
    Article(
        zotero_key=f"B{i}",
        title=f"immotherapy cohort {i} lung cancer",
        abstract=f"observational survival analysis {i}",
        authors="Bu B", journal="J2",
    )
    for i in range(1, 9)
]
_DECISIONS_SINGLE = {f"B{i}": "include" for i in range(1, 6)}

#: 单类守卫文案必须携带的标记（sklearn 原生文案一个都不含 → 撤守卫必红）。
_REJECT_MARKERS = ("两类训练标签", "include=5", "exclude=0", "补充 exclude 侧")


class F0402EmptyCandidatesModuleNails(unittest.TestCase):
    """F-04-02 模块级四预设对照：空候选 = order []/scores {} 合法终态。

    第 3 轮口径：短路**只对 svm 载体（svm/cjk）生效**——svm 空终态先于
    训练（n_features=0 + trained=False，含全 include/全 exclude 单类标签
    终态）；NB 载体（tuned/legacy）与 profile=None 沿用原有代码路径，
    model_meta 与旧版逐位一致（n_features=词表大小、无 trained 键，
    与同标签非空运行的 n_features 相等证明 fit 仍发生）。
    """

    #: (场景名, decisions)
    _SCENARIOS = (
        ("two-class", _DECISIONS_FULL),
        ("all-include", _DECISIONS_ALL_INCLUDE),
        ("all-exclude", _DECISIONS_ALL_EXCLUDE),
    )

    @staticmethod
    def _reference_n_features(profile):
        """同标签语料 + 1 条未筛候选的 n_features（词表只由训练文本决定，
        与候选无关）——空终态若仍走原路径，二者必须相等。"""
        articles = _ARTICLES_2CLASS + [Article(
            zotero_key="A6", title="extra unscreened cohort trial",
            abstract="extra randomized text", authors="Au A", journal="J")]
        result = ActiveLearningRanker(
            strategy="max", seed=42, min_labeled=5, profile=profile,
        ).rank(articles, _DECISIONS_FULL)
        assert result is not None
        return result.model_meta["n_features"]

    def test_empty_candidates_return_empty_terminal_state_all_profiles(self):
        for scenario, decisions in self._SCENARIOS:
            for profile in _PROFILES + (None,):
                with self.subTest(scenario=scenario, profile=profile):
                    result = ActiveLearningRanker(
                        strategy="max", seed=42, min_labeled=5, profile=profile,
                    ).rank(_ARTICLES_2CLASS, decisions)
                    # available（非 None）证明通过了 min_labeled 门槛，
                    # 走的是空候选终态而非 not_enough_labels。
                    self.assertIsNotNone(result, "5 条标签 >= min_labeled=5，不得返回 None")
                    self.assertEqual(result.order, [], "全筛完：order 必须为空（ranker 契约）")
                    self.assertEqual(result.scores, {}, "全筛完：scores 必须为空（ranker 契约）")
                    self.assertEqual(result.n_labeled, 5)
                    if profile in _SVM_PROFILES:
                        # svm 载体：终态先于训练，n_features=0 + trained=False
                        # （审计语义：未训练≠无特征，db.record_ranking 原样持久化）。
                        self.assertEqual(result.model_meta["n_features"], 0)
                        self.assertIs(result.model_meta["trained"], False)
                    else:
                        # NB 载体与默认路径（§10 不变量 1）：沿用原有代码路径，
                        # fit 照常发生——n_features 与同标签非空运行相等且 >0，
                        # 无 trained 键（model_meta 与旧版逐位一致）。
                        self.assertNotIn(
                            "trained", result.model_meta,
                            "默认/NB 路径不得新增 meta 键（不变量 1）")
                        self.assertGreater(result.model_meta["n_features"], 0)
                        self.assertEqual(
                            result.model_meta["n_features"],
                            self._reference_n_features(profile),
                            "空候选不得改变默认/NB 路径的 model_meta")

    def test_empty_terminal_still_validates_strategy(self):
        """空终态不豁免 strategy 校验（与 query_order 既有契约一致）。"""
        for profile in ("svm", None):
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    ActiveLearningRanker(
                        strategy="no-such-strategy", seed=42, min_labeled=5,
                        profile=profile,
                    ).rank(_ARTICLES_2CLASS, _DECISIONS_FULL)


class F0403SingleClassModuleNails(unittest.TestCase):
    """F-04-03 模块级对照：svm 载体可读拒绝 × NB 载体 -inf 降级。"""

    def test_svm_profiles_reject_single_class_with_readable_message(self):
        for profile in _SVM_PROFILES:
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError) as caught:
                    ActiveLearningRanker(
                        strategy="max", seed=42, min_labeled=5, profile=profile,
                    ).rank(_ARTICLES_1CLASS, _DECISIONS_SINGLE)
                # 专用异常类型（ValueError 子类）：HTTP 边界据此精确捕获
                # 转 4xx，不用会吞掉其他 ValueError 的宽捕获。撤守卫后
                # sklearn 抛的是原生 ValueError（非本类型）→ 此钉变红。
                if SingleClassTrainingError is None:
                    self.fail("SingleClassTrainingError 不存在（svm 守卫被撤）")
                self.assertIsInstance(caught.exception, SingleClassTrainingError)
                message = str(caught.exception)
                for marker in _REJECT_MARKERS:
                    self.assertIn(marker, message)
                # 可读文案 ≠ sklearn 原生内部措辞（撤守卫后会出现 solver 字样）
                self.assertNotIn("solver", message)

    def test_nb_profiles_degrade_single_class_to_constant_scores(self):
        """NB 对照（-inf 兜底语义）：单类不抛错，分数恒 1.0 仍可排序。"""
        for profile in _NB_PROFILES:
            with self.subTest(profile=profile):
                result = ActiveLearningRanker(
                    strategy="max", seed=42, min_labeled=5, profile=profile,
                ).rank(_ARTICLES_1CLASS, _DECISIONS_SINGLE)
                self.assertIsNotNone(result)
                self.assertEqual(set(result.order), {"B6", "B7", "B8"})
                self.assertEqual(
                    {key: value for key, value in result.scores.items()},
                    {"B6": 1.0, "B7": 1.0, "B8": 1.0},
                    "缺失类先验 -inf → P(include|x) 恒 1.0（nb.py 显式设计）",
                )


class SvmCarrierNails(unittest.TestCase):
    """F-04-02/F-04-03 载体级钉子（不经 ranker，直接钉 SVMClassifier）。"""

    ROWS = [{"beta": 2.0, "blocker": 1.0}, {"cohort": 3.0, "beta": 1.5}]

    def test_f0402_dict_mode_empty_predict_returns_empty_lists(self):
        clf = SVMClassifier(random_state=42).fit(self.ROWS, [1, 0])
        self.assertEqual(clf.predict_proba([]), [])
        self.assertEqual(clf.predict([]), [])
        # 正常路径不受影响：非空预测照旧（1 条、[0,1] 区间）。
        scores = clf.predict_proba([{"beta": 1.0}])
        self.assertEqual(len(scores), 1)
        self.assertGreaterEqual(scores[0], 0.0)
        self.assertLessEqual(scores[0], 1.0)

    def test_f0403_single_class_guard_message_carrier_level(self):
        for labels, expect_missing in (([1, 1], "exclude"), ([0, 0], "include")):
            with self.subTest(labels=labels):
                with self.assertRaises(ValueError) as caught:
                    SVMClassifier(random_state=42).fit(self.ROWS, labels)
                message = str(caught.exception)
                self.assertIn("两类训练标签", message)
                self.assertIn(f"补充 {expect_missing} 侧", message)
                self.assertNotIn("solver", message)

    def test_f0403_non_binary_labels_get_generic_two_class_message(self):
        with self.assertRaises(ValueError) as caught:
            SVMClassifier(random_state=42).fit(self.ROWS, ["a", "a"])
        self.assertIn("至少两个类别", str(caught.exception))

    def test_failed_fit_must_not_enable_empty_predict_short_circuit(self):
        """第 2 轮低危项：失败的 dict fit 不得留下半更新状态。

        修前（第 1 轮代码）：fit 先置 ``_dict_mode=True`` 再让
        ``_model.fit`` 抛错（如非法 sample_weight=[-1,-1]），随后
        ``predict([])``/``predict_proba([])`` 走零样本短路静默返回
        ``[]``——未 fit 状态的假成功。修后形态状态在 fit 成功后提交。
        注：sklearn 失败的 fit 可能已写入部分内部状态，因此后续报错
        类型依版本可为 ``ValueError``（2D 校验）而非 ``NotFittedError``；
        契约是**必须报错、绝不静默返回 ``[]``**（基线同抛 ValueError，
        本钉红于"状态先提交"的回归——即第 1 轮代码形态）。
        """
        from sklearn.exceptions import NotFittedError

        clf = SVMClassifier(random_state=42)
        with self.assertRaises(ValueError):
            clf.fit(self.ROWS, [1, 0], sample_weight=[-1, -1])
        # 未成功 fit：零样本短路不得生效（_dict_mode 仍为 None）。
        self.assertIsNone(clf._dict_mode)
        with self.assertRaises((NotFittedError, ValueError)):
            clf.predict_proba([])
        with self.assertRaises((NotFittedError, ValueError)):
            clf.predict([])

    # ------------------------------------------------------------------
    # R2 护栏：修复不得新增对合法输入的严格性 / 不得改变既有报错契约
    # ------------------------------------------------------------------

    def test_guard_unfitted_empty_input_still_raises_not_fitted(self):
        from sklearn.exceptions import NotFittedError

        clf = SVMClassifier(random_state=42)
        with self.assertRaises(NotFittedError):
            clf.predict_proba([])
        with self.assertRaises(NotFittedError):
            clf.predict([])

    def test_guard_skips_numpy_y_keeps_sklearn_equivalence(self):
        """numpy 数组 y 不拦截：仍由 sklearn 抛原生单类错误（直调等价）。"""
        import numpy as np

        with self.assertRaises(ValueError) as caught:
            SVMClassifier(random_state=42).fit(
                np.array([[1.0, 2.0], [3.0, 4.0]]), np.array([1, 1])
            )
        self.assertIn("solver", str(caught.exception))

    def test_array_mode_empty_list_predict_still_passthrough_sklearn(self):
        """数组模式空列表仍透传 sklearn 校验（ValueError，非本类拦截）。"""
        import numpy as np

        clf = SVMClassifier(random_state=42).fit(
            np.array([[1.0, 2.0], [3.0, 4.0]]), np.array([1, 0])
        )
        with self.assertRaises(ValueError):
            clf.predict_proba([])


class AlRankHttpNails(unittest.TestCase):
    """HTTP 层钉子：PUT profile=svm 后 GET /al/rank 两形态 200 / 4xx、非 500。

    真实 app + TestClient（raise_server_exceptions=False：撤修复时服务端
    异常以 500 响应形态现身使钉子变红，而不是在测试进程内抛出）。
    """

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from custom_backend import auth as auth_mod
        from custom_backend.main import app

        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # 不走 /api/auth/register：全套 pytest 共享注册限流桶，直接建用户
        # 签票（test_ai_suggest.py / test_r7_lastfix_api.py 同款口径）。
        user = auth_mod.create_user("r7svm@example.test", "R7-Svm-Nail-2026")
        cls.username = user["username"]
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _seed_task(self, name, articles, decisions):
        """建任务 + 直写筛选员库（articles/decisions），返回 (task_id, base)。"""
        from coscreen import db as db_mod
        from coscreen import tasks as tasks_mod
        from custom_backend import main as main_mod

        task_id = self.client.post(
            "/api/tasks", json={"name": name}, headers=self.auth,
        ).json()["task_id"]
        info = tasks_mod.find_task(task_id, str(main_mod.DATA_DIR))
        db = main_mod._screener_db(info, self.username)
        db_mod.upsert_articles(db, articles, [])
        for key, decision in decisions.items():
            db_mod.save_decision(db, key, decision, "", "", [])
        return task_id, f"/api/tasks/{task_id}"

    def test_f0402_empty_candidates_http_200_empty_order(self):
        task_id, base = self._seed_task(
            "svm-nail-f0402", _ARTICLES_2CLASS, _DECISIONS_FULL)
        put = self.client.put(f"{base}/al/profile", json={"profile": "svm"},
                              headers=self.auth)
        self.assertEqual(put.status_code, 200, put.text)
        response = self.client.get(f"{base}/al/rank", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIs(payload["available"], True)
        self.assertEqual(payload["order"], [], "全筛完的合法终态必须是 200+空序")
        self.assertEqual(payload["n_labeled"], 5)

    def test_f0402_all_include_empty_http_200_not_4xx(self):
        """第 2 轮阻断项的 HTTP 形态：全筛完 + 5 条全 include → 200 空序。

        第 1 轮残留：svm 在 fit 处抛单类 ValueError → 500；即便端点加了
        单类→4xx 转换，该**合法终态**也必须仍 200 空序（空终态先于训练，
        不触发单类拒绝）——钉死 200，防两类修复互相顶替。
        """
        task_id, base = self._seed_task(
            "svm-nail-f0402-1c", _ARTICLES_2CLASS, _DECISIONS_ALL_INCLUDE)
        put = self.client.put(f"{base}/al/profile", json={"profile": "svm"},
                              headers=self.auth)
        self.assertEqual(put.status_code, 200, put.text)
        response = self.client.get(f"{base}/al/rank", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIs(payload["available"], True)
        self.assertEqual(payload["order"], [])
        self.assertEqual(payload["n_labeled"], 5)

    def test_f0403_single_class_http_4xx_readable_not_500(self):
        task_id, base = self._seed_task(
            "svm-nail-f0403", _ARTICLES_1CLASS, _DECISIONS_SINGLE)
        put = self.client.put(f"{base}/al/profile", json={"profile": "svm"},
                              headers=self.auth)
        self.assertEqual(put.status_code, 200, put.text)
        response = self.client.get(f"{base}/al/rank", headers=self.auth)
        # 契约：用户补齐标签即可恢复 → 4xx（端点 except ValueError → 400），
        # 绝不 500（服务端故障语义）。端点转换未落位时本钉子以 500 变红。
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertLess(response.status_code, 500, response.text)
        detail = str(response.json().get("detail", ""))
        for marker in ("include=5", "exclude=0", "两类训练标签"):
            self.assertIn(marker, detail, "拒绝文案必须可读并指向用户动作")

    def test_f0403_single_class_shared_ranking_4xx_not_500(self):
        """第 2 轮补：/al/shared-ranking 同形态同契约（合并标签单类）。

        任务属主有 consensus:write（authorize_action 属主恒 True），同一
        单类语料经共享排序端点训练时同样必须 4xx + 可读文案，非 500。
        """
        task_id, base = self._seed_task(
            "svm-nail-shared", _ARTICLES_1CLASS, _DECISIONS_SINGLE)
        put = self.client.put(f"{base}/al/profile", json={"profile": "svm"},
                              headers=self.auth)
        self.assertEqual(put.status_code, 200, put.text)
        response = self.client.get(f"{base}/al/shared-ranking",
                                   headers=self.auth)
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertLess(response.status_code, 500, response.text)
        detail = str(response.json().get("detail", ""))
        for marker in ("include=5", "exclude=0", "两类训练标签"):
            self.assertIn(marker, detail, "拒绝文案必须可读并指向用户动作")

    def test_f0403_control_tuned_profile_http_200(self):
        """NB 载体对照：同一单类语料下 tuned 预设 200 且 3 条候选全序。"""
        task_id, base = self._seed_task(
            "svm-nail-f0403-ctl", _ARTICLES_1CLASS, _DECISIONS_SINGLE)
        put = self.client.put(f"{base}/al/profile", json={"profile": "tuned"},
                              headers=self.auth)
        self.assertEqual(put.status_code, 200, put.text)
        response = self.client.get(f"{base}/al/rank", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIs(payload["available"], True)
        self.assertEqual({item["zotero_key"] for item in payload["order"]},
                         {"B6", "B7", "B8"})
        self.assertTrue(all(item["score"] == 1.0 for item in payload["order"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
