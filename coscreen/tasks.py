"""多综述任务注册表与平铺库迁移（SPEC §17，方案一：任务目录 + task.json + 任务中枢）。

目录布局（默认 data_dir="data"）::

    data/
      tasks/{taskId}/            任务目录（taskId = sanitize(名) + "-" + 4hex，创建后不变）
        task.json                {display_name, description, screeners[], created_at,
                                  last_used_at, archived}
        {screener_slug}.db       每位筛选员一库（双盲不变）
        ui_settings_{slug}.json  每位筛选员的界面偏好
        pdfs/ coding_assets/ exports/ merge_out/
        {taskId}.lock            任务锁（O_EXCL，内容 = PID + 时间戳）
      .trash/{ts}_{taskId}/      删除任务的隔离区（移入而非就地 rm；默认保留
                                  REVIEWFLOW_TRASH_TTL_DAYS=30 天，触点式惰性
                                  清扫，<1 = 禁用自动清理=纯手工模式；
                                  purge_task 彻底删除**不经**此处）
      .purging/{name}.purging-{ts}/  purge 的原子改名暂存区（tasks 根之外；
                                  rmtree 未竟的残骸在此，不入注册表扫描）
      .migration_state.json      迁移进度状态（已处理的平铺库清单）

设计约定：
- 本模块为纯文件系统/SQLite 逻辑，**不 import streamlit**，可在 pytest 中独立单测；
- 所有 JSON 写入均为原子写（临时文件 + ``os.replace``）；
- ``task.json`` 损坏时降级为只读 TaskInfo（degraded=True，仅目录名可用），
  绝不静默覆盖用户数据；
- 平铺库（data/{task}_{screener}.db）在未登记时继续按旧规则工作 —— 登记表
  找不到任务时上层（ui/_helpers.resolve_db_path / ui/_settings）回退旧平铺规则，
  本模块自身不做任何路径合成。

关于 fulltexts.path 的重写口径：迁移搬移 PDF 后，行内路径改写为
``os.path.relpath(新位置, 应用根目录)``（与 save_pdf 经 pdfs_dir_for 对新行存储的
CWD 相对形式完全同 Flavor）——这样 ui/3 复筛工作台未改动的读取路径
（``Path(meta.path).read_bytes()``）在迁移后仍然可用；任务目录整体搬家的
可移植性由后续版本统一存储约定解决（见 SPEC §17 备注）。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import sys
import threading
import uuid
import zipfile
from contextlib import closing
from urllib.parse import quote
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from coscreen.security import sanitize_component

DEFAULT_SCREENER = "匿名"

#: 本模块日志（回收站清扫结果只进服务端日志，见 sweep_trash）
_LOG = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TASK_TOUCH_THROTTLE",
    "DEFAULT_TRASH_TTL_DAYS",
    "LOCK_SUFFIX",
    "MIGRATION_STATE_FILENAME",
    "PURGE_STAGING_DIRNAME",
    "PURGE_STAGING_SUFFIX",
    "PurgePartialError",
    "TASKS_DIRNAME",
    "TRASH_DIRNAME",
    "TRASH_TTL_ENV",
    "MigrationItem",
    "MigrationPlan",
    "TaskInfo",
    "acquire_task_lock",
    "archive_task",
    "create_task",
    "delete_task",
    "find_task",
    "find_task_by_display_name",
    "iter_fulltexts",
    "list_tasks",
    "lock_holder",
    "migration_state_path",
    "new_task_id",
    "pid_alive",
    "register_screener",
    "release_all_locks",
    "release_task_lock",
    "rename_task",
    "set_fulltext_path",
    "similar_task_names",
    "slugify_screener",
    "screener_options",
    "tasks_root",
    "touch_task",
    "trash_root",
    "trash_ttl_days",
    "sweep_trash",
    "purge_task",
    "migrate_scan",
    "apply_migration",
]

#: data/ 下任务注册表目录名
TASKS_DIRNAME = "tasks"
#: data/ 下删除隔离区目录名
TRASH_DIRNAME = ".trash"
#: 迁移状态文件名（记录在 data_dir 根，隐藏文件）
MIGRATION_STATE_FILENAME = ".migration_state.json"
#: 任务锁文件后缀（放在任务目录内：{taskId}.lock）
LOCK_SUFFIX = ".lock"
#: touch_task 的节流窗口（秒）：窗口内重复 touch 不重写 task.json
DEFAULT_TASK_TOUCH_THROTTLE = 60.0


# ---------------------------------------------------------------------------
# TaskInfo 与 task.json 读写（原子写；损坏降级）
# ---------------------------------------------------------------------------

@dataclass
class TaskInfo:
    """一个已登记任务的最小描述（list_tasks 的行对象）。"""

    task_id: str
    dir: Path                       # 任务目录 data/tasks/{taskId}
    display_name: str
    description: str = ""
    screeners: list[str] = field(default_factory=list)
    created_at: str = ""
    last_used_at: str = ""
    archived: bool = False
    #: task.json 损坏/缺失时的只读降级标记（此时仅目录名可信，禁止写回）
    degraded: bool = False
    #: 任务级 AL 模型预设名（coscreen.al.profiles.PROFILES 之一；
    #: None=未设置=旧版行为，规格书 2026-09-30 §4.1/§6.1）。由 API
    #: PUT /api/tasks/{task_id}/al/profile 写入 task.json 的 "al_profile"
    #: 字段；进 _json_payload round-trip，保证注册表写回（touch/rename/
    #: archive/register_screener 的 _mutate_task）不静默丢弃用户的显式选择。
    al_profile: str | None = None

    @property
    def db_files(self) -> list[Path]:
        """任务目录下现存的筛选员库文件（{slug}.db，按名称排序）。"""
        if not self.dir.is_dir():
            return []
        return sorted(p for p in self.dir.glob("*.db")
                      if p.is_file() and p.name != "collaboration.db")


def tasks_root(data_dir: str | Path = "data") -> Path:
    """任务注册表根目录：``{data_dir}/tasks``（只拼路径，不建目录）。"""
    return Path(data_dir) / TASKS_DIRNAME


def trash_root(data_dir: str | Path = "data") -> Path:
    """删除隔离区：``{data_dir}/.trash``（只拼路径，不建目录）。"""
    return Path(data_dir) / TRASH_DIRNAME


def migration_state_path(data_dir: str | Path = "data") -> Path:
    """迁移状态文件路径：``{data_dir}/.migration_state.json``。"""
    return Path(data_dir) / MIGRATION_STATE_FILENAME


def _task_json_path(task_dir: Path) -> Path:
    return task_dir / "task.json"


def _now_iso() -> str:
    """本地时间 ISO 串（秒级精度，排序友好）。"""
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _read_task_json(task_dir: Path) -> dict | None:
    """读取 task.json；文件缺失返回 None，损坏/非法 JSON 返回空 dict 由调用方降级。"""
    path = _task_json_path(task_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _atomic_write_json(path: Path, payload: dict) -> None:
    """原子写 JSON：同目录临时文件 + ``os.replace``（进程崩溃不留半截文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _write_task_json(task_dir: Path, payload: dict) -> None:
    _atomic_write_json(_task_json_path(task_dir), payload)


#: task.json 路径粒度的进程内互斥锁（_mutate_task 读-改-写串行化用）
_TASK_JSON_LOCKS: dict[Path, threading.Lock] = {}
_TASK_JSON_LOCKS_GUARD = threading.Lock()


def _task_json_lock(task_dir: Path) -> threading.Lock:
    """每个任务目录一把可重入锁（同名任务目录路径唯一）。"""
    with _TASK_JSON_LOCKS_GUARD:
        lock = _TASK_JSON_LOCKS.get(task_dir.resolve())
        if lock is None:
            lock = threading.RLock()
            _TASK_JSON_LOCKS[task_dir.resolve()] = lock
        return lock


def new_task_id(name: str) -> str:
    """由任务名派生 taskId：``sanitize(名)-4hex``（4 位十六进制随机尾巴）。

    创建后不再变化；重命名只改 task.json 的 display_name，目录与 taskId 不动。
    """
    stem = sanitize_component(name, "task")
    return f"{stem}-{uuid.uuid4().hex[:4]}"


def slugify_screener(name: str) -> str:
    """筛选员名 -> 任务目录内库文件/设置文件的 slug（清洗失败回退“匿名”）。"""
    return sanitize_component(name, DEFAULT_SCREENER)


def _degraded_info(task_dir: Path) -> TaskInfo:
    """task.json 缺失/损坏时的只读降级 TaskInfo（display_name 借用目录名）。"""
    return TaskInfo(
        task_id=task_dir.name,
        dir=task_dir,
        display_name=task_dir.name,
        degraded=True,
    )


def _info_from_json(task_dir: Path, raw: dict) -> TaskInfo:
    screeners = raw.get("screeners")
    al_profile = raw.get("al_profile")
    return TaskInfo(
        task_id=str(raw.get("task_id") or task_dir.name),
        dir=task_dir,
        display_name=str(raw.get("display_name") or task_dir.name),
        description=str(raw.get("description") or ""),
        screeners=[str(s) for s in screeners] if isinstance(screeners, list) else [],
        created_at=str(raw.get("created_at") or ""),
        last_used_at=str(raw.get("last_used_at") or ""),
        archived=bool(raw.get("archived", False)),
        # 非空字符串才视为已设置（非法类型按未设置降级，不抛错）
        al_profile=al_profile if isinstance(al_profile, str) and al_profile else None,
    )


def list_tasks(
    data_dir: str | Path = "data", include_archived: bool = False
) -> list[TaskInfo]:
    """扫描注册表：目录清单 × task.json 合并。

    - 排序：未归档在前，组内按 last_used_at 降序（并行按 task_id 稳定排序）；
      archived=True 的条目只在 include_archived=True 时返回，且排在其后；
    - task.json 损坏的目录产出 degraded TaskInfo（只读降级，视作未归档参与排序，
      last_used_at 为空排最后）——用户能在首页看到它并手工处理，绝不被静默隐藏。
    """
    root = tasks_root(data_dir)
    if not root.is_dir():
        return []
    infos: list[TaskInfo] = []
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        raw = _read_task_json(task_dir)
        if raw is None:
            continue  # 目录刚创建、还没有 task.json：跳过（create_task 的瞬态）
        if raw == {}:
            infos.append(_degraded_info(task_dir))
            continue
        infos.append(_info_from_json(task_dir, raw))
    active = [t for t in infos if not t.archived]
    archived = [t for t in infos if t.archived]
    key = lambda t: (t.last_used_at, t.task_id)  # noqa: E731
    active.sort(key=key, reverse=True)
    archived.sort(key=key, reverse=True)
    return active + archived if include_archived else active


def find_task(task_id: str, data_dir: str | Path = "data") -> TaskInfo | None:
    """按 taskId 找任务（含已归档；找不到/损坏降级目录同样返回）。"""
    task_dir = tasks_root(data_dir) / task_id
    # Path("..").name == ".."：name 检查拦不住字面量上跳，需显式拒绝（P2-7）
    if task_id in {".", ".."} or not task_dir.is_dir() or Path(task_id).name != task_id:
        return None
    raw = _read_task_json(task_dir)
    if raw is None:
        return None
    return _degraded_info(task_dir) if raw == {} else _info_from_json(task_dir, raw)


def find_task_by_display_name(
    name: str, data_dir: str | Path = "data"
) -> TaskInfo | None:
    """按 display_name 精确匹配找任务（含已归档，供路径解析桥接）。

    只读操作：注册表目录不存在或任何 IO 异常都返回 None（调用方回退旧平铺规则）。
    """
    needle = (name or "").strip()
    if not needle:
        return None
    try:
        for info in list_tasks(data_dir, include_archived=True):
            if info.display_name == needle:
                return info
    except OSError:
        return None
    return None


def similar_task_names(
    name: str, data_dir: str | Path = "data"
) -> list[str]:
    """与给定名字「相似」的已有任务名（新建任务对话框的同名相似提示）。

    相似 = 去空格后完全相同，或清洗后的文件名分量相同，或大小写不敏感互为子串。
    确定性排序（字母序），供 UI 提示，不做硬拦截。
    """
    needle = (name or "").strip()
    if not needle:
        return []
    stem = sanitize_component(needle, "task").lower()
    hits: list[str] = []
    try:
        existing = list_tasks(data_dir, include_archived=True)
    except OSError:
        return []
    for info in existing:
        other = info.display_name.strip()
        low = other.lower()
        if (
            other == needle
            or sanitize_component(other, "task").lower() == stem
            or (stem in low or low in stem) and bool(stem) and bool(low)
        ):
            hits.append(info.display_name)
    return sorted(set(hits))


# ---------------------------------------------------------------------------
# 注册表 CRUD
# ---------------------------------------------------------------------------

def create_task(
    name: str, description: str = "", data_dir: str | Path = "data"
) -> TaskInfo:
    """新建任务：建目录 + 原子写 task.json。

    - 空名（或清洗后为空）-> ValueError；
    - 与已有任务同名（去空格比较，含归档）-> ValueError（UI 层给相似提示）；
    - 返回新建的 TaskInfo（screeners 为空，由 register_screener 逐个登记）。
    """
    clean = (name or "").strip()
    if not clean or not sanitize_component(clean, ""):
        raise ValueError("任务名不能为空。")
    for info in list_tasks(data_dir, include_archived=True):
        if info.display_name.strip() == clean:
            raise ValueError(f"已有同名任务：{info.display_name}")
    task_id = new_task_id(clean)
    task_dir = tasks_root(data_dir) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    info = TaskInfo(
        task_id=task_id,
        dir=task_dir,
        display_name=clean,
        description=(description or "").strip(),
        created_at=_now_iso(),
        last_used_at=_now_iso(),
    )
    _write_task_json(task_dir, _json_payload(info))
    return info


def _json_payload(info: TaskInfo) -> dict:
    payload = {
        "task_id": info.task_id,
        "display_name": info.display_name,
        "description": info.description,
        "screeners": list(info.screeners),
        "created_at": info.created_at,
        "last_used_at": info.last_used_at,
        "archived": info.archived,
    }
    # al_profile 仅在已设置时写入：未设置任务的 payload 与升级前逐字节一致
    if info.al_profile:
        payload["al_profile"] = info.al_profile
    return payload


def _mutate_task(
    task_id: str, data_dir: str | Path, mutate
) -> TaskInfo:
    """读-改-写 task.json 的公共骨架（重命名/归档/登记筛选员/touch 共用）。

    以 task.json 路径为粒度的进程内互斥：并发调用（如 register_screener 与
    touch_task 交错）不再互相覆盖——后写者基于先写者的最新快照继续改。
    单进程后端（uvicorn 单进程）内这已是全部竞态来源；写盘本身原子
    （temp + os.replace，见 _atomic_write_json）。
    """
    info0 = find_task(task_id, data_dir)
    if info0 is None:
        raise ValueError(f"任务不存在：{task_id}")
    with _task_json_lock(info0.dir):
        info = find_task(task_id, data_dir)
        if info is None:
            raise ValueError(f"任务不存在：{task_id}")
        if info.degraded:
            raise ValueError("任务描述文件（task.json）已损坏，该任务为只读降级状态。")
        mutate(info)
        # 已知字段以 TaskInfo 为准刷新，其余扩展字段（protocol / living_review_auto
        # 等，由各功能模块经读-改-写写入 task.json）原样透传——注册表写回
        # 不再静默丢弃其他模块保存的数据。
        raw = _read_task_json(info.dir) or {}
        raw.update(_json_payload(info))
        _write_task_json(info.dir, raw)
        return info


def rename_task(task_id: str, new_name: str, data_dir: str | Path = "data") -> TaskInfo:
    """重命名任务：只改 task.json 的 display_name（目录与 taskId 不变）。"""
    clean = (new_name or "").strip()
    if not clean:
        raise ValueError("任务名不能为空。")
    for info in list_tasks(data_dir, include_archived=True):
        if info.task_id != task_id and info.display_name.strip() == clean:
            raise ValueError(f"已有同名任务：{info.display_name}")
    return _mutate_task(
        task_id, data_dir, lambda info: setattr(info, "display_name", clean)
    )


def archive_task(
    task_id: str, data_dir: str | Path = "data", archived: bool = True
) -> TaskInfo:
    """归档/恢复任务（archived 标记；侧栏与任务卡默认只列未归档）。"""
    return _mutate_task(task_id, data_dir, lambda info: setattr(info, "archived", archived))


def register_screener(
    task_id: str, name: str, data_dir: str | Path = "data"
) -> TaskInfo:
    """登记筛选员（display 名进入 task.json screeners；幂等，保序追加）。"""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("筛选员名不能为空。")

    def _add(info: TaskInfo) -> None:
        if clean not in info.screeners:
            info.screeners.append(clean)

    return _mutate_task(task_id, data_dir, _add)


def unregister_screener(
    task_id: str, name: str, data_dir: str | Path = "data"
) -> TaskInfo:
    """移除筛选员登记（R6-C1：撤销角色时回收 task.json 的 screeners 行；
    幂等——名不在列表时不变）。"""
    clean = (name or "").strip()

    def _remove(info: TaskInfo) -> None:
        if clean in info.screeners:
            info.screeners.remove(clean)

    return _mutate_task(task_id, data_dir, _remove)


def touch_task(
    task_id: str,
    data_dir: str | Path = "data",
    throttle: float = DEFAULT_TASK_TOUCH_THROTTLE,
) -> bool:
    """更新 last_used_at（节流：距上次写入不足 throttle 秒则跳过，避免频繁重写）。

    返回是否真正写入；任务不存在/损坏降级时静默返回 False（不打断 UI）。
    """
    info = find_task(task_id, data_dir)
    if info is None or info.degraded:
        return False
    now = _now_iso()
    if info.last_used_at and throttle > 0:
        try:
            from datetime import datetime

            last = datetime.fromisoformat(info.last_used_at)
            delta = datetime.fromisoformat(now) - last
            if delta.total_seconds() < throttle:
                return False
        except ValueError:
            pass
    return _mutate_task(
        task_id, data_dir, lambda info: setattr(info, "last_used_at", now)
    ) is not None


def delete_task(task_id: str, data_dir: str | Path = "data") -> Path:
    """删除任务 = 整目录移入 ``{data_dir}/.trash/{ts}_{taskId}/``（可手工找回）。

    任务被其他活进程持锁时拒绝删除（ValueError）；返回隔离区内的目标路径。

    - **U5-P1**：锁检查按规范任务目录（``tasks_root/{info.task_id}``）进行，
      ``task.`` 等 Windows 别名路径不再绕过/错检规范锁；
    - **U5-P2**：本进程持锁（后端请求期任务锁）且规范名在途计数 >1 时同样
      拒绝——DELETE 请求自身占 1，>1 说明还有导入/分析等操作在跑。计数钩子
      由 ``register_inflight_counter`` 注册（custom_backend 中间件）；未注册
      （CLI/测试直调）时保持旧行为（本进程持锁不算占用）。
    """
    info = find_task(task_id, data_dir)
    if info is None:
        raise ValueError(f"任务不存在：{task_id}")
    canonical_dir = tasks_root(data_dir) / info.task_id
    lock_path = canonical_dir / f"{info.task_id}{LOCK_SUFFIX}"
    holder = lock_holder(lock_path)
    # 本进程持锁（后端请求期任务锁，见 custom_backend 路由中间件）不算占用；
    # 仅其他存活进程持有时拒绝。
    if (holder and holder.get("pid") != os.getpid()
            and pid_alive(int(holder.get("pid", 0)))):
        raise ValueError(
            f"任务正被其他窗口使用（PID {holder.get('pid')}），请先关闭后再删除。"
        )
    if holder and holder.get("pid") == os.getpid():
        inflight = 0
        if _inflight_requests is not None:
            try:  # 计数钩子异常不阻断删除（退回旧行为：仅本请求持锁）
                inflight = int(_inflight_requests(info.task_id))
            except Exception:  # noqa: BLE001  防御性兜底
                inflight = 0
        if inflight > 1:
            raise ValueError("任务有操作进行中（导入/分析等），请等待完成后再删除。")
    dst = trash_root(data_dir) / f"{_now_iso().replace(':', '')}_{info.task_id}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(info.dir), str(dst))
    return dst


# ---------------------------------------------------------------------------
# 彻底删除（purge）与回收站保留期清扫（2.1.5 任务删除策略，契约 =
# out/release_plan/drafts/frontend_contract_2.1.5-task-delete.md）
# ---------------------------------------------------------------------------

#: 回收站默认保留天数（前端披露文案的 N；运维可用环境变量覆盖）
DEFAULT_TRASH_TTL_DAYS = 30
#: 回收站保留期环境变量名：整数字符串；<1 = 禁用自动清理（纯手工模式）；
#: 缺省/非整数 → 默认 30（警告进日志，不让坏配置静默变成「永不清理」或 500）
TRASH_TTL_ENV = "REVIEWFLOW_TRASH_TTL_DAYS"
#: purge 暂存区目录名（{data_dir}/.purging，位于 tasks 根**之外**——rmtree
#: 未竟的残骸不会被 list_tasks 扫到、也不参与建任务重名检查）
PURGE_STAGING_DIRNAME = ".purging"
#: purge 暂存条目后缀（{dirName}.purging-{ts}，位于暂存区内、同卷）
PURGE_STAGING_SUFFIX = ".purging-"


def trash_ttl_days() -> int:
    """回收站保留天数：环境变量优先（整数），缺省/非法回退默认 30。

    返回 <1 表示**禁用自动清理**（sweep_trash 立即空转）——纯手工模式，
    运维自行处置 ``.trash``；契约写死该口径。
    """
    raw = os.environ.get(TRASH_TTL_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_TRASH_TTL_DAYS
    try:
        return int(raw.strip())
    except ValueError:
        _LOG.warning("%s=%r 不是整数，回退默认 %d 天", TRASH_TTL_ENV, raw,
                     DEFAULT_TRASH_TTL_DAYS)
        return DEFAULT_TRASH_TTL_DAYS


def _trash_entry_time(entry: Path):
    """回收站条目的时间：目录名前缀 ``{ts}_``（delete_task 的秒级本地时间串）
    优先；解析失败回退目录 mtime；两者都拿不到返回 None（清扫按跳过处理，
    保守不删）。"""
    from datetime import datetime

    try:
        return datetime.strptime(entry.name.split("_", 1)[0], "%Y-%m-%dT%H%M%S")
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(entry.stat().st_mtime)
    except OSError:
        return None


def sweep_trash(
    data_dir: str | Path = "data",
    *,
    ttl_days: int | None = None,
    now=None,
) -> dict:
    """惰性清扫回收站：保留期满的 ``.trash/{ts}_{taskId}/`` 目录整目录 rmtree。

    - 过期口径：条目时间 ≤ ``now − TTL``（恰好满 TTL 天即过期）；
    - 目录名时间戳不可解析 → mtime 兜底；两者皆不可得 → 跳过（保守不删）；
    - 只处理**目录**：``.trash`` 里的 ``migration_backup_*.zip`` 等文件不动；
    - 单目录失败计入 ``skipped`` 后继续（不因一个坏目录让触点请求 500）；
    - 返回 ``{"purged": n, "skipped": n, "ttl_days": t}``（+禁用时
      ``disabled=True``）——只供服务端日志，触点响应不扩；``now``/``ttl_days``
      仅供测试注入，生产触点一律走环境变量口径。
    """
    from datetime import timedelta

    ttl = trash_ttl_days() if ttl_days is None else int(ttl_days)
    if ttl < 1:
        return {"purged": 0, "skipped": 0, "ttl_days": ttl, "disabled": True}
    root = trash_root(data_dir)
    if not root.is_dir():
        return {"purged": 0, "skipped": 0, "ttl_days": ttl}
    if now is None:
        from datetime import datetime

        now = datetime.now()
    cutoff = now - timedelta(days=ttl)
    purged = skipped = 0
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue  # 迁移备份 zip 等文件不属清扫对象
        ts = _trash_entry_time(entry)
        if ts is None:
            skipped += 1
            continue
        if ts > cutoff:
            continue  # 保留期内
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            skipped += 1
            _LOG.warning("回收站条目清扫失败（跳过继续）：%s： %s", entry.name, exc)
            continue
        purged += 1
    return {"purged": purged, "skipped": skipped, "ttl_days": ttl}


class PurgePartialError(OSError):
    """purge 已把任务目录移出注册表、但暂存目录 rmtree 未竟（不可恢复路径上
    的受控失败）——携带暂存目录路径与剩余条目数，端点按 409 如实报告。"""

    def __init__(self, staging: Path):
        remaining = sum(1 for _ in staging.rglob("*")) if staging.exists() else 0
        super().__init__(
            f"任务目录已移出注册表，暂存目录 {staging.name} 清理未完成"
            f"（剩余 {remaining} 个条目）")
        self.staging = staging
        self.remaining = remaining


def _rmtree_with_readonly_recovery(path: Path) -> None:
    """rmtree；失败时清树内只读位后重试一次（Windows 只读文件 unlink 失败
    是 purge 半删的最常见诱因）。清位是 best-effort，仍失败则如实抛
    OSError 给上层报告状态。"""
    try:
        shutil.rmtree(path)
    except OSError:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    p.chmod(p.stat().st_mode | stat.S_IWUSR)
                except OSError:
                    pass
        shutil.rmtree(path)


def purge_task(task_id: str, data_dir: str | Path = "data") -> Path:
    """彻底删除任务 = 任务目录整目录清除，**不进 ``.trash``（不可恢复）。

    防误删闸门与 delete_task 同一道（锁检查逐字复用）：任务不存在 / 其他
    活进程持任务锁 / 本进程在途写 >1 → ValueError（端点映射 409，任务原样
    保留）。执行顺序：

    ① 删除目标 = 属主校验/404 校验命中的**同一目录**（``info.dir``）。
       **不**按 task.json 的 task_id 合成目标（评审 P2：手工拷贝/备份还原
       等场景 json 内 id 可与目录名不一致，合成目标会错删另一个任务）；
       并校验其解析路径严格位于 tasks_root 之下（防路径拼接意外把注册表
       外的目录 rmtree）。
    ② 原子改名到暂存区 ``{data_dir}/.purging/{dirName}.purging-{ts}``
       （同卷 rename；暂存区在 tasks 根**之外**——rmtree 未竟的残骸不会被
       list_tasks 扫到、也不撞建任务重名检查）。rename 失败（如句柄未释放）
       抛 OSError：任务目录**原样保留**在注册表，绝不半删。
    ③ rmtree 暂存条目（先清只读位再重试一次）；仍未竟抛 PurgePartialError
       （任务目录已移出注册表，暂存条目路径/剩余条目数如实报告，交运维
       处置）。

    返回被清除的暂存条目路径（仅供日志与测试断言）。
    """
    info = find_task(task_id, data_dir)
    if info is None:
        raise ValueError(f"任务不存在：{task_id}")
    canonical_dir = tasks_root(data_dir) / info.task_id
    lock_path = canonical_dir / f"{info.task_id}{LOCK_SUFFIX}"
    holder = lock_holder(lock_path)
    # 本进程持锁（后端请求期任务锁，见 custom_backend 路由中间件）不算占用；
    # 仅其他存活进程持有时拒绝。
    if (holder and holder.get("pid") != os.getpid()
            and pid_alive(int(holder.get("pid", 0)))):
        raise ValueError(
            f"任务正被其他窗口使用（PID {holder.get('pid')}），请先关闭后再删除。"
        )
    if holder and holder.get("pid") == os.getpid():
        inflight = 0
        if _inflight_requests is not None:
            try:  # 计数钩子异常不阻断删除（退回旧行为：仅本请求持锁）
                inflight = int(_inflight_requests(info.task_id))
            except Exception:  # noqa: BLE001  防御性兜底
                inflight = 0
        if inflight > 1:
            raise ValueError("任务有操作进行中（导入/分析等），请等待完成后再删除。")
    # ①目标=属主校验命中的目录（info.dir），且必须严格位于 tasks_root 之下
    # （== tasks_root 本身或逃逸到外面都拒绝——绝无可能 rmtree 注册表之外
    # 的任何目录）。
    target = info.dir
    root = tasks_root(data_dir).resolve()
    resolved = target.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"非法任务路径：{info.task_id}")
    if not target.is_dir():
        raise ValueError(f"任务目录不存在：{info.task_id}")
    # ②原子改名到暂存区（同卷；失败=任务原样保留，不产生半删）
    staging_root = Path(data_dir) / PURGE_STAGING_DIRNAME
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / (
        f"{target.name}{PURGE_STAGING_SUFFIX}{_now_iso().replace(':', '')}")
    os.rename(target, staging)
    # ③清除暂存条目；失败如实报告其状态（任务已移出注册表）
    try:
        _rmtree_with_readonly_recovery(staging)
    except OSError as exc:
        raise PurgePartialError(staging) from exc
    return staging


# ---------------------------------------------------------------------------
# 筛选员选项（注册表 ∪ 现存 {slug}.db）
# ---------------------------------------------------------------------------

def screener_options(
    info: TaskInfo | None,
    data_dir: str | Path = "data",
    extra: Iterable[str] = (),
) -> list[str]:
    """侧栏筛选员下拉的候选名单（确定性排序）。

    - 注册表 screeners（登记顺序）在前；
    - 任务目录里现存 {slug}.db 的 slug 不在登记名单时以 slug 本名补在后（按名排序）
      ——兼容手工拷贝进来的库文件；
    - ``extra``（如当前会话筛选员）若缺失则插在最前，保证当前值永远可选。
    """
    names: list[str] = list(info.screeners) if info else []
    if info is not None:
        known = {slugify_screener(n) for n in names}
        for db in info.db_files:
            slug = db.stem
            if slug not in known and slug:
                names.append(slug)
                known.add(slug)
    out: list[str] = []
    for n in names:
        if n and n not in out:
            out.append(n)
    for n in extra:
        if (n or "").strip() and n not in out:
            out.insert(0, n)
    return out


# ---------------------------------------------------------------------------
# 任务锁（O_EXCL + PID/时间戳陈旧检测）
# ---------------------------------------------------------------------------

#: 本进程持有的锁（路径 -> PID），atexit 兜底释放
_HELD_LOCKS: dict[Path, int] = {}

#: 同进程在途请求计数钩子（U5-P2：custom_backend 中间件注册；返回该任务
#: 规范名当前在途请求数。未注册时 delete_task 保持旧行为）。
_inflight_requests: Callable[[str], int] | None = None


def register_inflight_counter(fn: Callable[[str], int] | None) -> None:
    """注册/注销同进程任务在途请求计数函数（delete_task 的互斥依据）。"""
    global _inflight_requests
    _inflight_requests = fn


def pid_alive(pid: int) -> bool:
    """进程是否存活。

    POSIX：``os.kill(pid, 0)`` 探测（EPERM 视为存活，查无此进程视为死亡）。
    Windows（P1-2 修复，2026-11-11）：``os.kill(pid, 0)`` 在 Windows 语义下走
    ``GenerateConsoleCtrlEvent(CTRL_C_EVENT=0)``，对已退出/无控制台的 PID 抛
    WinError 87（OSError，不在原捕获清单）→ 异常上冒被调用方按「锁被持有」
    处理 → 崩溃/被杀进程残留的任务锁永久 409（发布前对抗审查 P1 复现：
    200→退出留锁→409→删锁恢复）。改用 ctypes ``OpenProcess``+
    ``WaitForSingleObject``（0 秒等待）：WAIT_TIMEOUT → 存活；
    WAIT_OBJECT_0 → 已终止；``ERROR_ACCESS_DENIED``/``WAIT_FAILED`` → 状态
    未知，保守视为存活（对齐 POSIX EPERM 口径）；其余失败（含 87=PID 不
    存在）→ 死亡。PID 先做域钳制（sec29）：``0``=Windows 空闲进程、负数
    或超 32 位无符号域的值只可能来自损坏/伪造的锁文件，直接判死——
    POSIX 侧同时堵住 ``kill(-1, 0)``（全进程组探测）误判存活的口子。
    """
    pid = int(pid)
    if pid <= 0 or pid > 0xFFFFFFFF:
        return False
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, ValueError, TypeError):
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Windows 探活（P1-2），语义见 :func:`pid_alive` 文档。"""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    # PROCESS_QUERY_LIMITED_INFORMATION(0x1000) | SYNCHRONIZE(0x00100000)
    handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
    if not handle:
        # 5=ERROR_ACCESS_DENIED：进程存在但无权查询——保守视为存活（对齐
        # POSIX EPERM 口径）；87=ERROR_INVALID_PARAMETER 等其余=PID 不存在
        # → 死亡。（对抗评审 HIGH 修正：初版此处比较方向写反——87 判活会让
        # 被系统完全回收的崩溃残留锁永不接管（P1 换形态复发），5 判死会让
        # 活着的受保护/他户进程锁被抢占。）
        return ctypes.get_last_error() == 5
    try:
        # WAIT_TIMEOUT(258)=仍在运行；WAIT_OBJECT_0(0)=已终止——用等待结果
        # 替代 GetExitCodeProcess 的退出码判断，消除 STILL_ACTIVE(259) 与
        # 真实退出码同值的歧义（评审 low#1）。WAIT_FAILED(0xFFFFFFFF)=等待
        # 本身失败（句柄竞态失效等），进程状态未知：保守判活（sec29），
        # 否则受保护/他户进程的活锁会被当死锁抢占。
        result = kernel32.WaitForSingleObject(handle, 0)
        return True if result == 0xFFFFFFFF else result == 258
    finally:
        kernel32.CloseHandle(handle)


def lock_holder(lock_path: str | Path) -> dict | None:
    """读锁文件内容 -> ``{"pid": int, "ts": str}``；无锁/损坏返回 None。"""
    path = Path(lock_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    parts = text.split()
    if not parts:
        return None
    try:
        return {"pid": int(parts[0]), "ts": parts[1] if len(parts) > 1 else ""}
    except ValueError:
        return None


def _lock_is_ours(lock_path: Path) -> bool:
    holder = lock_holder(lock_path)
    return bool(holder and holder.get("pid") == os.getpid())


def acquire_task_lock(
    task_id: str, data_dir: str | Path = "data", task_dir: str | Path | None = None
) -> Path | None:
    """尝试获取任务锁（O_EXCL 创建 ``{taskId}.lock``，内容 = "PID ISO时间戳"）。

    - 已被**存活**进程持锁 -> 返回 None（调用方只提示，不做硬阻塞）；
    - 锁文件存在但 PID 已死（上次进程崩溃残留）-> 陈旧锁，接管（删除后重建）；
    - 本进程已持有同一把锁 -> 幂等返回锁路径。
    """
    directory = Path(task_dir) if task_dir is not None else tasks_root(data_dir) / task_id
    lock_path = directory / f"{task_id}{LOCK_SUFFIX}"
    if _lock_is_ours(lock_path) and task_id in {p.stem for p in _HELD_LOCKS}:
        return lock_path
    for _attempt in range(2):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
            )
        except FileExistsError:
            holder = lock_holder(lock_path)
            if holder and pid_alive(int(holder.get("pid", 0))):
                return None  # 活进程持有：只读提示，不抢
            # sec29（DS·锁与写闸）：判死到删除之间锁内容可能被并发接管者
            # 重写——删除前复核，内容与判定时不一致则保守放弃本轮，避免
            # 按陈旧快照误删他人的活锁（重建仍由 O_EXCL 仲裁并行接管者）。
            if lock_holder(lock_path) != holder:
                return None
            try:
                lock_path.unlink()  # 陈旧锁接管
            except FileNotFoundError:
                continue  # 并发接管者已删：直接争 O_EXCL 重建
            except OSError:
                return None  # 权限/共享冲突：本轮放弃，调用方按忙重试
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {_now_iso()}")
        _HELD_LOCKS[lock_path] = os.getpid()
        return lock_path
    return None


def release_task_lock(lock_path: str | Path, force: bool = False) -> bool:
    """释放锁：仅当内容确认是本进程 PID 时删除（force=True 无条件删除，供手动接管）。"""
    path = Path(lock_path)
    if not path.exists():
        _HELD_LOCKS.pop(path, None)
        return False
    if not force and not _lock_is_ours(path):
        return False
    try:
        path.unlink()
    except OSError:
        return False
    _HELD_LOCKS.pop(path, None)
    return True


def release_all_locks() -> None:
    """进程退出兜底：释放本进程持有的全部任务锁（atexit 注册）。"""
    for path in list(_HELD_LOCKS):
        release_task_lock(path)


atexit.register(release_all_locks)


# ---------------------------------------------------------------------------
# fulltexts 行读取与路径改写（迁移专用；不改动 coscreen/fulltext.py 的存储逻辑）
# ---------------------------------------------------------------------------

_FULLTEXT_QUERY_COLUMNS = ("zotero_key", "filename", "path", "sha256")


def iter_fulltexts(db_path: str | Path) -> list[dict]:
    """读 fulltexts 表全部行（表不存在/库损坏时返回空列表，不抛异常）。

    以 ``mode=ro`` 只读打开：WAL 库的读写连接在关闭时会 checkpoint 并删除
    -wal/-shm 附属文件 —— 扫描/备份阶段绝不能有这样的副作用。
    """
    # quote：路径含 %/?/# 时未编码会被 SQLite URI 语法吃掉（P2-6）
    uri = f"file:{quote(str(Path(db_path).resolve()))}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            rows = conn.execute(
                "SELECT zotero_key, filename, path, sha256 FROM fulltexts"
            ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(zip(_FULLTEXT_QUERY_COLUMNS, row)) for row in rows]


def set_fulltext_path(db_path: str | Path, zotero_key: str, new_path: str) -> None:
    """改写一行的 fulltexts.path（迁移重写专用）。"""
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.execute(
            "UPDATE fulltexts SET path = ? WHERE zotero_key = ?", (new_path, zotero_key)
        )


def resolve_fulltext_raw(
    raw: str, db_path: str | Path, data_dir: str | Path = "data"
) -> Path | None:
    """把 fulltexts.path 的历史取值解析为真实文件路径（找不到返回 None）。

    历史行可能是 绝对路径、应用根相对（data/pdfs/x.pdf）或任务目录相对
    （pdfs/x.pdf）：按 应用根 CWD -> data_dir 父目录 -> 数据库父目录 的固定
    顺序探测，返回第一个存在的候选；全部不存在时返回 None。
    """
    p = Path(raw)
    if p.is_absolute():
        return p if p.is_file() else None
    bases = (Path.cwd(), Path(data_dir).resolve().parent, Path(db_path).parent)
    seen: set[Path] = set()
    for base in bases:
        cand = (base / p).resolve()
        if cand in seen:
            continue
        seen.add(cand)
        if cand.is_file():
            return cand
    return None


# ---------------------------------------------------------------------------
# 迁移：平铺库扫描与整理
# ---------------------------------------------------------------------------

@dataclass
class MigrationItem:
    """一个待迁移的平铺库（data/*.db）。"""

    db_path: Path
    task_name: str
    screener: str
    needs_review: bool = False
    reason: str = ""


@dataclass
class MigrationPlan:
    """一次 migrate_scan 的结果（items 顺序 = 文件名排序，确定性）。"""

    items: list[MigrationItem] = field(default_factory=list)
    data_dir: Path = Path("data")


def _read_meta_pair(db_path: Path) -> dict:
    """只读打开库的 meta 表取 task_name/screener；任何失败返回空 dict。"""
    try:
        with closing(sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)) as conn:
            rows = dict(
                conn.execute(
                    "SELECT key, value FROM meta WHERE key IN ('task_name','screener')"
                ).fetchall()
            )
        return {str(k): str(v) for k, v in rows.items()}
    except sqlite3.Error:
        return {}


def _parse_flat_db(db_path: Path) -> MigrationItem:
    """从 meta 表 + 文件名推断 (任务名, 筛选员)；歧义时标 needs_review。

    - meta 表 task_name/screener 齐备 -> 直接采用（最可信）；
    - 仅 task_name -> 文件名须以 sanitize(task) + "_" 开头，后缀即筛选员；
    - meta 缺失 -> 文件名按第一个 "_" 拆两段；拆不开或超过两段 -> needs_review
      （任务名本身允许含下划线，故 >1 个下划线即真歧义）。
    """
    stem = db_path.name[:-len(".db")] if db_path.name.lower().endswith(".db") else db_path.name
    meta = _read_meta_pair(db_path)
    task_name = (meta.get("task_name") or "").strip()
    screener = (meta.get("screener") or "").strip()
    if task_name and screener:
        return MigrationItem(db_path, task_name, screener)
    if task_name:
        prefix = f"{sanitize_component(task_name, task_name)}_"
        if stem.startswith(prefix) and len(stem) > len(prefix):
            return MigrationItem(db_path, task_name, stem[len(prefix):])
        return MigrationItem(
            db_path, task_name, screener or "", True,
            reason="meta 表缺少筛选员，且文件名无法与任务名前缀对齐",
        )
    parts = stem.split("_")
    if len(parts) == 2 and all(parts):
        return MigrationItem(db_path, parts[0], parts[1])
    if len(parts) == 1:
        return MigrationItem(
            db_path, stem, "", True, reason="文件名不含分隔符，无法拆分任务与筛选员"
        )
    return MigrationItem(
        db_path, parts[0], "_".join(parts[1:]), True,
        reason="文件名含多个下划线，任务/筛选员拆分有歧义（需人工确认）",
    )


def migrate_scan(
    data_dir: str | Path = "data", skip_registered: bool = True
) -> MigrationPlan:
    """扫描 data_dir 顶层的平铺 *.db，产出迁移计划（不修改任何文件）。

    - 跳过 data/tasks/**、隐藏文件与 -wal/-shm 附属文件；
    - ``skip_registered=True`` 时剔除已出现在迁移状态文件里的库（应用过迁移后
      横幅不再重复出现；状态文件损坏视作空）。
    """
    root = Path(data_dir)
    items: list[MigrationItem] = []
    if root.is_dir():
        for db in sorted(root.glob("*.db")):
            if db.name in ("auth.db", "users.db"):
                continue  # 系统库（认证/用户）不参与任务迁移扫描
            if not db.is_file():
                continue
            items.append(_parse_flat_db(db))
    plan = MigrationPlan(items=items, data_dir=root)
    if skip_registered:
        done = _done_db_names(root)
        plan.items = [it for it in plan.items if it.db_path.name not in done]
    return plan


def _done_db_names(data_dir: Path) -> set[str]:
    state = _read_state(data_dir)
    return {
        str(entry.get("db"))
        for entry in state.get("items", [])
        if isinstance(entry, dict) and entry.get("db")
    }


def _read_state(data_dir: Path) -> dict:
    path = migration_state_path(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": []}
    return raw if isinstance(raw, dict) else {"items": []}


def _write_state(data_dir: Path, state: dict) -> None:
    _atomic_write_json(migration_state_path(data_dir), state)


def _flat_aux_names(item: MigrationItem) -> list[str]:
    """与该平铺库同名的附属文件（settings / 决策导出 CSV，全部用清洗后命名）。"""
    t = sanitize_component(item.task_name, "task")
    s = sanitize_component(item.screener or DEFAULT_SCREENER, DEFAULT_SCREENER)
    return [
        f"ui_settings_{t}_{s}.json",
        f"decisions_{t}_{s}.csv",
        f"decisions_{s}.csv",
        f"decisions2_{t}_{s}.csv",
        f"decisions2_{s}.csv",
    ]


def _collect_move_files(items: list[MigrationItem], data_dir: Path) -> list[Path]:
    """整理（move=True）前收集全部将被搬移的平铺文件（备份 zip 的清单）。"""
    files: list[Path] = []
    flat_pdfs = (data_dir / "pdfs").resolve()
    for item in items:
        if item.needs_review:
            continue
        files.append(item.db_path)
        for suffix in ("-wal", "-shm"):
            side = item.db_path.with_name(item.db_path.name + suffix)
            if side.is_file():
                files.append(side)
        for name in _flat_aux_names(item):
            p = data_dir / name
            if p.is_file():
                files.append(p)
        for row in iter_fulltexts(item.db_path):
            resolved = resolve_fulltext_raw(row["path"], item.db_path, data_dir)
            if resolved is not None and flat_pdfs in resolved.parents:
                files.append(resolved)
    uniq: list[Path] = []
    seen: set[Path] = set()
    for p in files:
        rp = p.resolve()
        if rp not in seen:  # 集合去重：此前每文件重 resolve 全表（O(n²)，n=4000 时 65s）
            seen.add(rp)
            uniq.append(p)
    return uniq


def _backup_zip(files: list[Path], data_dir: Path) -> Path:
    """搬移前把将被移动的平铺文件打包到 data/.trash/（任一失败则中止迁移）。"""
    stamp = _now_iso().replace(":", "")
    backup = trash_root(data_dir) / f"migration_backup_{stamp}.zip"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backup, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            arc = os.path.relpath(p, data_dir)
            zf.write(p, arcname=arc)
    return backup


def _move_quietly(src: Path, dest_dir: Path, dest_name: str | None = None) -> Path:
    """移动单个文件；目标重名时自动加序号（绝不静默覆盖）。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = dest_name or src.name
    target = dest_dir / name
    if target.exists():
        stem, suffix = os.path.splitext(name)
        i = 2
        while target.exists():
            target = dest_dir / f"{stem}_{i}{suffix}"
            i += 1
    shutil.move(str(src), str(target))
    return target


def _organize_pdfs(item: MigrationItem, db_path: Path, task_dir: Path,
                   data_dir: Path, entry: dict) -> None:
    """把平铺 data/pdfs 中属于该库的 PDF 按任务归组，并重写 fulltexts.path。"""
    flat_pdfs = (data_dir / "pdfs").resolve()
    for row in iter_fulltexts(db_path):
        raw = row["path"]
        resolved = resolve_fulltext_raw(raw, db_path, data_dir)
        if resolved is not None and flat_pdfs not in resolved.parents:
            # 不在平铺 pdfs 区的（用户自选目录）：保持原样，不做归组
            continue
        name = row["filename"] or (resolved.name if resolved else Path(raw).name)
        target = task_dir / "pdfs" / name
        if resolved is not None and flat_pdfs in resolved.parents:
            if target.exists():
                if _sha256_of(target) == _sha256_of(resolved):
                    pass  # 同名同内容：直接改写指针
                else:
                    entry["errors"].append(
                        f"{name}: 任务目录已有同名但内容不同的 PDF，未覆盖"
                    )
                    continue
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(resolved), str(target))
                    entry["moved"].append(str(resolved))
                except (shutil.Error, OSError) as exc:
                    entry["errors"].append(f"{name}: 移动失败（{exc}）")
                    continue
        elif resolved is None and not target.exists():
            entry["errors"].append(f"{name}: 找不到原文件，路径未改写")
            continue
        elif resolved is None and target.exists():
            # 原文件已被同任务另一位筛选员的迁移先搬走：校验 sha 后直接共享该文件
            row_sha = (row["sha256"] or "").strip().lower()
            if row_sha and row_sha != _sha256_of(target):
                entry["errors"].append(
                    f"{name}: 平铺文件已被归组且内容不一致，请人工核对"
                )
                continue
        new_value = (
            str(target) if task_dir.is_absolute()
            else os.path.relpath(target, Path.cwd())
        )
        try:
            set_fulltext_path(db_path, row["zotero_key"], new_value)
            entry["moved"].append(f"fulltexts:{row['zotero_key']}")
        except sqlite3.Error as exc:
            entry["errors"].append(f"{name}: 路径改写失败（{exc}）")


def _sha256_of(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_migration(
    plan: MigrationPlan,
    data_dir: str | Path = "data",
    move: bool = False,
) -> dict:
    """执行迁移计划：逐项登记任务；``move=True`` 时把文件整理进任务目录。

    - needs_review 项一律跳过（等人工在 UI 确认后再扫描）；
    - move=True：先把全部将被搬移的平铺文件备份 zip 到 data/.trash/（备份失败
      中止且不做任何移动），再逐项搬 db(+wal/shm) / 同名 settings / decisions CSV /
      data/pdfs 里的 PDF（按 fulltexts 归组 + path 改写）；单个文件被占用等错误
      **逐项记录并继续**，绝不中断整批；
    - 结果合并进 data/.migration_state.json（按平铺库名去重，横幅据此消失）；
    - 返回 ``{"backup": Path|None, "results": [entry, ...]}``。
    """
    root = Path(data_dir)
    backup: Path | None = None
    if move:
        move_items = [it for it in plan.items if not it.needs_review]
        backup = _backup_zip(_collect_move_files(move_items, root), root)
    results: list[dict] = []
    for item in plan.items:
        if item.needs_review:
            results.append({
                "db": item.db_path.name, "action": "skipped", "ok": False,
                "errors": [item.reason or "needs_review"],
            })
            continue
        try:
            info = find_task_by_display_name(item.task_name, root) or create_task(
                item.task_name, data_dir=root
            )
            if item.screener:
                register_screener(info.task_id, item.screener, data_dir=root)
            info = find_task(info.task_id, root) or info
        except (ValueError, OSError) as exc:
            results.append({
                "db": item.db_path.name, "action": "register", "ok": False,
                "errors": [str(exc)],
            })
            continue
        entry = {
            "db": item.db_path.name, "task_id": info.task_id,
            "action": "organize" if move else "register",
            "ok": True, "moved": [], "errors": [],
        }
        if move:
            slug = slugify_screener(item.screener or DEFAULT_SCREENER)
            dest_db = info.dir / f"{slug}.db"
            try:
                moved_db = _move_quietly(item.db_path, info.dir, f"{slug}.db")
                entry["moved"].append(str(moved_db))
            except (shutil.Error, OSError) as exc:
                entry["ok"] = False
                entry["errors"].append(f"数据库移动失败（{exc}）")
                results.append(entry)
                continue
            for suffix in ("-wal", "-shm"):
                side = item.db_path.with_name(item.db_path.name + suffix)
                if side.is_file():
                    try:
                        entry["moved"].append(
                            str(_move_quietly(side, info.dir, moved_db.name + suffix))
                        )
                    except (shutil.Error, OSError) as exc:
                        entry["errors"].append(f"{side.name}: {exc}")
            for name in _flat_aux_names(item):
                p = root / name
                if not p.is_file():
                    continue
                dest_dir = info.dir / "exports" if name.startswith("decisions") else info.dir
                target_name = f"ui_settings_{slug}.json" if name.startswith("ui_settings_") else name
                try:
                    entry["moved"].append(str(_move_quietly(p, dest_dir, target_name)))
                except (shutil.Error, OSError) as exc:
                    entry["errors"].append(f"{name}: {exc}")
            _organize_pdfs(item, moved_db, info.dir, root, entry)
            touch_task(info.task_id, data_dir=root, throttle=0)
        results.append(entry)
    state = _read_state(root)
    known = _done_db_names(root)
    ok_results = [e for e in results if e.get("ok")]
    for entry in ok_results:
        known.add(entry["db"])
    merged = [e for e in state.get("items", []) if isinstance(e, dict)
              and e.get("db") not in known]
    state["items"] = merged + ok_results  # 只记成功项：失败项留给下次扫描重试
    state["applied_at"] = _now_iso()
    _write_state(root, state)
    return {"backup": backup, "results": results}
