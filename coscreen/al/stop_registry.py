"""停止规则注册表（SPEC 2026-09-30 升级规格 §5.3）。

全库停止规则的唯一权威清单，供 API（``custom_backend/main.py`` 停止
规则端点）与前端停止面板读取。纯数据模块：

- 不 import 任何 coscreen 模块（无循环依赖）、不做任何 I/O，
  import 时零副作用（不连库、不起服务、不开网络）；
- 仅描述各规则的元信息（label/description/废弃与推荐标记），
  不实现规则本身——规则实现分别在
  :mod:`coscreen.al.stop`（streak，已降级为进度提示）与
  :mod:`coscreen.al.stop_certificate`（RecallCertificate，安全停止）；
- "manual" 无实现模块：用户自行判断何时停止。

键约定：
  - ``label``：用户可见名称（中文，走 UI 时由调用方按需经 T() 处理）；
  - ``description``：一句话说明（含安全语义限定）；
  - ``deprecated``：仅 streak 为 True（S1 全量分析证明无安全默认值，
    升级规格 §5.2 降级为进度提示）；
  - ``recommended``：仅 certificate 为 True（S1 推荐的安全停止规则）。
"""

from __future__ import annotations

__all__ = ["STOP_RULES"]

STOP_RULES = {
    "manual": {"label": "手动停止", "description": "用户自行判断何时停止"},
    "streak": {"label": "连续排除提示", "description": "连续排除 N 条后提醒(仅进度提示,非安全保证)", "deprecated": True},
    "certificate": {"label": "召回证书(推荐)", "description": "以 ≥95% 置信度保证总召回 ≥95%", "recommended": True},
}
