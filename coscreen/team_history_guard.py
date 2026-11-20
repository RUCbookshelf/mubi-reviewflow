# -*- coding: utf-8 -*-
"""裁决后历史只读保护事实读取（S2 历史守卫纯读取核心）。

本模块只回答一个问题：给定**调用方已开启事务的协作库连接**、task 与界面
阶段（``screen``/``fulltext``/``coding``），哪些条目的历史写回必须被拒绝。
它不接 HTTP、不做路径 I/O、不写任何行、不自行开连接/事务，也不判断当前
proof 是否仍适用、当前名册是否变化、当前轮次是否一致。

事实来源严格限定为 S1b 授权 attestation 及其引用的 generic 仲裁快照：

- 只经 :func:`coscreen.screening_arbitration.list_decision_arbitration_attestations_on`
  读取 ``task_id``/``stage`` 精确匹配的原始授权事实（含调用方事务内未提交
  行）；不另开连接、不读 TSR 当前适用记录集合。
- 只采信 generic ``records`` 中 ``arbitration_status == "adjudicated"`` 且
  ``final_decision`` 为 ``include``/``exclude`` 的行；该行 ``zotero_key``
  必须被同一 attestation 的 ``input_proof`` 覆盖。普通共识行、未裁决行、
  无 attestation 的 generic/CSV/legacy 历史都不是锁来源。
- task/stage 不混用；旧 round 的已被证明的裁决不会被当前 round 过滤掉；
  proof 版本陈旧、当前成员或当前 proof 变化都不会解锁历史。
- 底层 typed unavailable/corrupt/orphan/``ProgrammingError`` 原样透传；
  命中 adjudicated 行却缺有效键、非二选一终局或 proof 未覆盖等结构矛盾，
  以 :class:`coscreen.screening_arbitration.ArbitrationAttestationStoreUnavailable`
  拒绝，绝不把坏历史降级成「未裁决」。

本模块为后续 B2/C 路由的**纯事实读取原语**：消费者仍须在真实业务写入
前、同一保护窗口内调用；本模块自身零写入，整批请求先校验后判定，不做
部分成功。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from coscreen.screening_arbitration import (
    ArbitrationAttestationStoreUnavailable,
    ArbitrationConnectionContractError,
    list_decision_arbitration_attestations_on,
)

__all__ = [
    "TeamHistoryReadOnlyError",
    "adjudicated_history_keys_on",
    "assert_history_writable_on",
]

#: 界面阶段到仲裁存储阶段的显式映射。``coding`` 没有本筛选仲裁来源，
#: 因此不在映射内（返回空集合，不猜测额外编码裁决规则）。
_STAGE_TO_ARBITRATION_STAGE = {
    "screen": "stage1",
    "fulltext": "stage2",
}

_VALID_STAGES = frozenset({*_STAGE_TO_ARBITRATION_STAGE, "coding"})

#: 仲裁终局只接受二选一（与 screening_arbitration.ARBITRATION_DECISIONS 同域）。
_BINARY_FINALS = frozenset({"include", "exclude"})


class TeamHistoryReadOnlyError(RuntimeError):
    """请求写回的历史条目已受生效裁决保护（HTTP 层后续映射 409）。

    ``item_keys`` 是**请求键与受保护集合的交集**，已排序去重；不是该
    task/stage 的全部受保护键（避免把未请求的条目泄露给调用方）。
    """

    code = "history_read_only_after_arbitration"
    message_key = "team.history.read_only_after_arbitration"

    def __init__(
        self,
        task_id: str,
        stage: str,
        item_keys: Iterable[str],
    ) -> None:
        self.task_id = task_id
        self.stage = stage
        self.item_keys = tuple(sorted(set(item_keys)))
        super().__init__("已裁决，历史记录仅供查看。")


def _require_borrowed_transaction(conn: sqlite3.Connection, *, api: str) -> None:
    """借用连接契约：原生连接且调用方已有事务；否则 RuntimeError。

    本函数不降级、不暗开事务、不建 SAVEPOINT；调用方修复后重试。
    """
    if not isinstance(conn, sqlite3.Connection):
        raise ArbitrationConnectionContractError(
            f"{api} 需要既有协作库连接（sqlite3.Connection，借用连接，不自行打开）。")
    if not conn.in_transaction:
        raise ArbitrationConnectionContractError(
            f"调用方必须已开启事务再调用 {api}；本函数不自行开启事务。")


def _validated_task_id(task_id: Any) -> str:
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id 必须为非空文本")
    return task_id


def _arbitration_stage(stage: Any) -> str | None:
    """返回 ``stage1``/``stage2``；``coding`` 返回 None；非法值 ValueError。"""
    if not isinstance(stage, str) or stage not in _VALID_STAGES:
        raise ValueError("stage 必须为 screen、fulltext 或 coding")
    return _STAGE_TO_ARBITRATION_STAGE.get(stage)


def _materialize_item_keys(item_keys: Any) -> tuple[str, ...]:
    """一次性物化并逐项校验请求键，避免生成器被消费两次或半批先判定。"""
    if isinstance(item_keys, (str, bytes, bytearray)):
        raise ValueError("item_keys 必须为文本键的可迭代集合，不能传单个字符串")
    try:
        iterator = iter(item_keys)
    except TypeError as exc:
        raise ValueError("item_keys 必须为可迭代的文本键集合") from exc
    materialized: list[str] = []
    for key in iterator:
        if not isinstance(key, str) or not key:
            raise ValueError("item_keys 的每一项必须为非空文本")
        materialized.append(key)
    return tuple(materialized)


def _structural_unavailable(detail: str) -> ArbitrationAttestationStoreUnavailable:
    return ArbitrationAttestationStoreUnavailable(
        "adjudicated arbitration history is structurally inconsistent: " + detail)


def _adjudicated_keys_from_history(
    found: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    """从已按 task/stage 过滤的 attestation 组合行提取受保护键。

    只把「adjudicated + 二选一终局 + proof 覆盖」三条件同时成立的行加入
    锁集合。非 adjudicated 行（agreed/unresolved/未知状态）不参与；一旦
    adjudicated 行缺键、终局非二选一或 proof 未覆盖，则整次读取 fail-closed，
    不把坏历史当作未裁决。
    """
    protected: set[str] = set()
    for combined in found:
        if not isinstance(combined, Mapping):
            raise _structural_unavailable("attestation/record 组合行不是对象")
        attestation = combined.get("attestation")
        record = combined.get("record")
        if not isinstance(attestation, Mapping) or not isinstance(record, Mapping):
            raise _structural_unavailable("attestation/record 组合行形状非法")
        attestation_id = attestation.get("id")
        record_id = record.get("id")
        context = f"attestation={attestation_id!r} record={record_id!r}"
        proof = attestation.get("input_proof")
        if not isinstance(proof, Mapping):
            raise _structural_unavailable(f"{context} 的 input_proof 不是对象")
        if record.get("stage") != attestation.get("stage"):
            raise _structural_unavailable(
                f"{context} 的 generic record stage 与 attestation stage 不一致")
        rows = record.get("records")
        if not isinstance(rows, list):
            raise _structural_unavailable(f"{context} 的 records 不是数组")
        for row in rows:
            if not isinstance(row, Mapping):
                raise _structural_unavailable(f"{context} 的 records 含非对象行")
            if row.get("arbitration_status") != "adjudicated":
                continue
            key = row.get("zotero_key")
            if not isinstance(key, str) or not key.strip():
                raise _structural_unavailable(
                    f"{context} 的 adjudicated 行缺有效非空 zotero_key")
            final = row.get("final_decision")
            if final not in _BINARY_FINALS:
                raise _structural_unavailable(
                    f"{context} 的 adjudicated 行 final_decision 非二选一: {final!r}")
            per_user = proof.get(key)
            if not isinstance(per_user, Mapping) or not per_user:
                raise _structural_unavailable(
                    f"{context} 的 adjudicated 键未被 input_proof 有效覆盖: {key!r}")
            protected.add(key)
    return frozenset(protected)


def adjudicated_history_keys_on(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    stage: str,
) -> frozenset[str]:
    """返回该 task/stage 下已生效裁决保护的条目键集合（只读、零写入）。

    借用连接契约：``conn`` 必须是原生 ``sqlite3.Connection`` 且调用方已
    开启事务；否则 :class:`ArbitrationConnectionContractError`（RuntimeError）。
    不打开新连接、不 BEGIN/COMMIT/ROLLBACK/CLOSE、不执行 DDL、不改
    ``row_factory``。读取与调用方同一事务快照，因此可见其未提交的
    attestation。

    ``stage`` 仅接受 ``screen``/``fulltext``/``coding``：前两者显式映射到
    仲裁存储的 ``stage1``/``stage2``；``coding`` 没有本筛选仲裁来源，返回
    空集合。``task_id`` 必须为非空文本，否则 ``ValueError``。

    底层 ``ArbitrationAttestationStoreUnavailable``/损坏/孤儿/
    ``sqlite3.ProgrammingError`` 原样透传；缺 attestation 表按既有 loader
    合法返回空。adjudicated 行结构矛盾同样以
    ``ArbitrationAttestationStoreUnavailable`` 拒绝。
    """
    _require_borrowed_transaction(conn, api="adjudicated_history_keys_on")
    task_id = _validated_task_id(task_id)
    arbitration_stage = _arbitration_stage(stage)
    if arbitration_stage is None:
        return frozenset()
    found = list_decision_arbitration_attestations_on(
        conn, stage=arbitration_stage, task_id=task_id)
    return _adjudicated_keys_from_history(found)


def assert_history_writable_on(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    stage: str,
    item_keys: Iterable[str],
) -> None:
    """请求写回前断言：任一 ``item_keys`` 已受裁决保护则整次拒绝。

    批量语义：先一次性物化并校验**全部**请求键，再读取受保护集合；任何
    一个键命中即抛 :class:`TeamHistoryReadOnlyError`（携带排序去重的交集
    键），调用方不得部分成功。未命中返回 None。函数自身零写入，不修改
    调用方事务、连接生命周期或 ``row_factory``。

    参数非法（``task_id``/键非非空文本、``stage`` 非
    ``screen``/``fulltext``/``coding``、``item_keys`` 为单个字符串或
    不可迭代）→ ``ValueError``。连接契约与底层存储错误语义同
    :func:`adjudicated_history_keys_on`。
    """
    _require_borrowed_transaction(conn, api="assert_history_writable_on")
    task_id = _validated_task_id(task_id)
    arbitration_stage = _arbitration_stage(stage)
    requested = _materialize_item_keys(item_keys)
    if arbitration_stage is None:
        return
    protected = adjudicated_history_keys_on(
        conn, task_id=task_id, stage=stage)
    blocked = tuple(sorted(set(requested) & protected))
    if blocked:
        raise TeamHistoryReadOnlyError(task_id, stage, blocked)
