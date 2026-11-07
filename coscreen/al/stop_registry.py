"""停止规则注册表（SPEC 2026-09-30 升级方案 §5.3；口径副本见
``docs/methodology/recall_certificate.md`` §2、§6）。

全库停止规则的元信息清单，由 ``GET /api/tasks/{task_id}/al/stop-rules``
（``custom_backend/main.py``）原样下发。纯数据模块：

- 不 import 任何 coscreen 模块（无循环依赖）、不做任何 I/O，
  import 时零副作用（不连库、不起服务、不开网络）；
- 仅描述各规则的元信息（label/description/废弃与推荐标记），
  不实现规则本身——规则实现分别在
  :mod:`coscreen.al.stop`（streak，已降级为进度提示）与
  :mod:`coscreen.al.stop_certificate`（RecallCertificate，安全停止）；
- "manual" 无实现模块：用户自行判断何时停止。

**消费面（如实声明，勿再声称前端读取）**：截至 2.1.4 只有一个消费方
——上面的 ``stop-rules`` 端点原样透传。前端 ``al_stop_panel.js`` 只请求
``/al/stop-certificate/check``，从不读本注册表；用户可见的安全文案在
端点 message、面板与 ``custom_backend/main.py`` 三处（已与
:mod:`coscreen.al.stop_certificate` 的实算置信度对齐）。本模块因此是
"元数据单一来源"，**不是**用户可见文案的单一来源。

键约定（r9 b10·F2-38：三条规则键集必须齐全，缺键语义按 ``False``
读取——旧的"某条规则缺 ``deprecated``/``recommended`` 键"形态已消除）：

  - ``label``：用户可见名称（中文，走 UI 时由调用方按需经 T() 处理）；
  - ``description``：一句话说明（含安全语义限定）；
  - ``deprecated``：``bool``，True 表示该规则已降级、不该作安全默认值；
  - ``recommended``：``bool``，True 表示推荐的安全停止规则。

消费方应把两条布尔量当**完整键**读（``rule["deprecated"]``），
``.get(..., False)`` 亦等价。
"""

from __future__ import annotations

__all__ = ["STOP_RULES"]

# 每条规则一行（保持 2.1.4 既有形态：既有测试 test_r8_fl4_locale.py 按
# 「首个含 certificate 条目标识的行」检查 description，跨行会漏检）。
STOP_RULES = {
    "manual": {"label": "手动停止", "description": "用户自行判断何时停止", "deprecated": False, "recommended": False},
    "streak": {"label": "连续排除提示", "description": "连续排除 N 条后提醒(仅进度提示,非安全保证)", "deprecated": True, "recommended": False},
    "certificate": {"label": "召回证书(推荐)", "description": "抽验未见新相关时检验总召回 ≥95%；置信度按超几何随已发现相关数与抽验量变化，并非固定 95%", "deprecated": False, "recommended": True},
}
