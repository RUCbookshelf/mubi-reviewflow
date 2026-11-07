# -*- coding: utf-8 -*-
"""r14 补扫（前端）静态钉：在途 server-merge × 任务切换、筛选参与权跨任务串台。

两个缺陷都在 ``custom_frontend/index.html`` 的单页状态机上，行为面需要浏览器
时序才能完整复现；本文件把「修复必须存在的守卫」钉成源码契约（破坏即红）：

1. **在途 server-merge × 任务切换**（补扫 medium）
   - 请求发出时记录 ``taskId`` 与一个自增代次；``await`` 之后必须同时复查
     「仍是同一任务」与「代次未被推进」，否则**不得**写 ``S.merge``/渲染/
     报『已从服务器加载…』成功文案；
   - 任务切换（``pickTask`` 复位段）必须推进代次并 abort 在途请求、清掉
     ``S.mergeTaskId``，使旧任务响应无法落到新任务的合并卡片；
   - 成功路径必须把 ``S.mergeServerSource`` 置回 ``true``（复位段会清 false，
     不恢复会让随后的保存走 CSV 分支）；
   - 保存守卫在服务器模式下应引导「重新从服务器加载」而不是只提示重传 CSV。

2. **筛选参与权跨任务串台**（补扫 low）
   - ``S.screeningEnabled`` 必须在任务切换时复位（新任务未知 → 乐观默认
     ``true``），避免把任务 A 的「无权限」引导渲染到任务 B 的初筛页；真实值
     仍由 ``refreshProgress`` / 服务端 403 校正。

运行：``python -m pytest build/tests/test_r14_sweep_frontend_static.py -q``
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "custom_frontend" / "index.html"
SOURCE = INDEX.read_text(encoding="utf-8")


def _slice(start: str, end: str) -> str:
    begin = SOURCE.index(start)
    finish = SOURCE.index(end, begin)
    return SOURCE[begin:finish]


def _pick_task_body() -> str:
    return _slice("async function pickTask(", "function renderScreenerSel(")


def _server_merge_handler() -> str:
    return _slice("$('#mergeServerLoad').addEventListener('click'",
                  "$('#mergeFiles').addEventListener('change'")


def _merge_save_handler() -> str:
    return _slice("$('#mergeSave').addEventListener('click'",
                  "$('#mergeLatest').addEventListener('click'")


class ServerMergeTaskSwitchRaceTests(unittest.TestCase):
    def test_handler_records_task_and_generation_before_await(self):
        handler = _server_merge_handler()
        self.assertRegex(
            handler, r"const\s+taskId\s*=\s*S\.task\.task_id",
            "server-merge 处理器必须在发请求前记录发起任务")
        self.assertRegex(
            handler, r"(const\s+seq\s*=\s*\+\+mergeLoadSeq|\+\+mergeLoadSeq)",
            "server-merge 处理器必须为在途请求取一个自增代次")
        self.assertLess(
            handler.index("mergeLoadSeq"),
            handler.index("await api("),
            "代次必须在 await 之前取得")

    def test_response_is_discarded_when_task_or_generation_changed(self):
        handler = _server_merge_handler()
        after_await = handler[handler.index("await api("):]
        guard = re.search(
            r"if\s*\(([^)]*)\)\s*return;", after_await)
        self.assertIsNotNone(
            guard, "await 之后必须有丢弃过期响应的守卫（任务/代次复查）")
        condition = guard.group(1)
        self.assertIn("mergeLoadSeq", condition,
                      "守卫必须复查代次：%r" % condition)
        self.assertRegex(
            condition, r"S\.task\?\.task_id\s*!==\s*taskId",
            "守卫必须复查仍是发起任务：%r" % condition)
        # 守卫必须先于写状态/渲染（否则旧任务数据已经渲染进新任务页面）。
        self.assertLess(
            after_await.index(guard.group(0)),
            after_await.index("S.merge = d"),
            "复查必须在写 S.merge 之前")

    def test_success_path_restores_server_source_flag(self):
        handler = _server_merge_handler()
        after_await = handler[handler.index("await api("):]
        self.assertRegex(
            after_await, r"S\.mergeServerSource\s*=\s*true",
            "成功路径必须把 serverSource 置回 true（复位段已清 false）")

    def test_pick_task_invalidates_inflight_request(self):
        body = _pick_task_body()
        self.assertRegex(
            body, r"mergeLoadSeq\s*\+\+|S\.mergeTaskId\s*=\s*null",
            "任务切换必须推进 server-merge 代次/清 mergeTaskId")
        self.assertIn("S.mergeTaskId = null", body,
                      "任务切换后旧任务的 mergeTaskId 必须失效")
        self.assertRegex(
            body, r"\.abort\(\)",
            "任务切换必须 abort 在途 server-merge 请求")

    def test_save_guard_points_server_mode_to_reload(self):
        handler = _merge_save_handler()
        guard = handler[:handler.index("if (!S.mergeServerSource")]
        self.assertRegex(
            guard, r"runtimeMode\s*===\s*'server'",
            "保存守卫在服务器模式下应引导重新从服务器加载（旧任务响应被丢弃后"
            "mergeServerSource 已复位为 false，CSV 分支文案会误导）")


class ScreeningEnabledTaskSwitchTests(unittest.TestCase):
    def test_pick_task_resets_screening_enabled(self):
        body = _pick_task_body()
        self.assertRegex(
            body, r"S\.screeningEnabled\s*=\s*true",
            "任务切换必须复位 S.screeningEnabled（新任务参与权未知，乐观默认 true），"
            "否则任务 A 的『无权限』引导会串到任务 B")
