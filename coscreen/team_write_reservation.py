"""Team 写入保留连接 —— S2-B1 纯 stdlib 连接资源层。

本模块只实现“同时保留多个现存 SQLite 库的写连接，并在唯一目标提交后
释放其余输入”这一资源/事务边界；不包含任何团队业务窗口、角色、资格、
名册、schema 或历史裁决逻辑。

行为合同：

- 入口用专用 ``mode=rw`` URI 打开现存 collaboration 库（绝不创建目录
  或库），设置 ``sqlite3.Row``，并立即 ``BEGIN IMMEDIATE``；不迁移、
  不执行 ``executescript``、不设置 ``journal_mode`` 等副作用 PRAGMA。
  ``timeout`` 只作为 SQLite busy timeout 交给 ``sqlite3.connect``。
- :meth:`ReservedWriteConnections.reserve` 只允许调用一次：先 owner，
  其余输入按规范路径排序；所有给定文件必须存在；按物理文件身份
  （``st_dev`` + ``st_ino``，必要时 ``samefile``）去重，collab、owner
  与硬链接别名若指向同一物理库则复用同一连接/事务。每个独立输入同为
  ``mode=rw`` + ``sqlite3.Row`` + ``BEGIN IMMEDIATE``。
- 全部输入保持写事务直到唯一目标连接真正 ``commit()`` 返回；随后其余
  输入 ``rollback``，全部连接按获取顺序逆序 ``close``。正常退出只提交
  目标一次，不表示跨库分布式原子。
- 业务或 commit 异常不自动重放：异常前不提交；清理异常不得遮盖原异常。
  目标 commit 已成功后，清理故障只追加到 :attr:`cleanup_errors`，并
  尽力完成其他清理，不把业务成功谎报为回滚或清理失败。
- 未选目标即正常退出、改选其他目标、二次 ``reserve``、选择未登记目标
  均属编程错误（``RuntimeError``）；缺失库文件为 ``FileNotFoundError``，
  且不会创建任何库。

刻意不包含：task RLock、notify/SSE、角色/资格/schema/prepared 状态
校验、HTTP 入口。低层接受受信业务窗口内部传入的路径；高层仍必须在
collab 锁内读取名册后再枚举输入。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["ReservedWriteConnections", "retained_write_connections"]

_URI_SUFFIX = "?mode=rw"


def _path_key(path: Path) -> str:
    """规范路径键：绝对化 + 解析链接/符号 + 平台大小写归一。"""
    return os.path.normcase(os.fspath(Path(path).resolve()))


def _sort_key(path: Path) -> str:
    return _path_key(path)


def _physical_identity(path: Path) -> tuple[object, ...] | None:
    """物理文件身份（设备号 + inode/文件索引）；不可 ``stat`` 时返回 ``None``。

    ``st_ino`` 为 0 的文件系统（部分 FAT/网络卷）退化为规范路径身份，
    由 :meth:`ReservedWriteConnections._registered_connection` 的
    ``samefile`` 兜底继续完成硬链接去重。
    """
    try:
        info = Path(path).stat()
    except OSError:
        return None
    inode = getattr(info, "st_ino", 0)
    if inode:
        return ("inode", getattr(info, "st_dev", 0), inode)
    return ("path", _path_key(Path(path)))


def _describe_failure(action: str, label: str, exc: BaseException) -> str:
    try:
        detail = f"{type(exc).__name__}: {exc}"
    except BaseException:  # pragma: no cover - 防御异常自身 __str__
        detail = type(exc).__name__
    return f"{action} failed for {label}: {detail}"


def _open_reserved_connection(path: Path, timeout: float) -> sqlite3.Connection:
    """打开一个现存库的专用写连接，并在其上 ``BEGIN IMMEDIATE``。

    只接受现存文件；``mode=rw`` 在 SQLite 层同样拒绝创建。``foreign_keys``
    是连接本地约束开关（非持久/journal 模式），与既有 ``db._connect``、
    ``coding_consensus._connect`` 的写连接保持一致；设置后立即读回验证
    为 1，防止驱动/连接未接受该开关时静默降级。``BEGIN`` 或 FK 校验失败
    时关闭刚建立的连接后原样上抛（调用方负责清理更早打开的其他连接）。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"保留连接要求现存的 SQLite 库文件，拒绝创建：{path}")
    conn = sqlite3.connect(
        path.resolve().as_uri() + _URI_SUFFIX, uri=True, timeout=timeout)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise sqlite3.OperationalError(
                f"failed to enable foreign_keys on reserved connection: {path}")
        conn.execute("BEGIN IMMEDIATE")
    except BaseException:
        try:
            conn.close()
        except BaseException:
            pass
        raise
    return conn


class ReservedWriteConnections:
    """一次保留窗口内的连接与事务句柄。

    只能由 :func:`retained_write_connections` 创建并在其 ``with`` 体内使用。
    事务所有权归本对象：调用方不得提交/回滚/关闭这些连接，也不得自行
    ``BEGIN``。唯一提交点是上下文管理器在正常退出时对所选目标调用一次
    ``commit()``。
    """

    def __init__(self, collab_path: Path, *, timeout: float = 15.0) -> None:
        self._collab_path = Path(collab_path)
        self._timeout = timeout
        self._collab_conn: sqlite3.Connection | None = None
        self._connections: list[sqlite3.Connection] = []
        self._labels: dict[int, str] = {}
        self._aliases: dict[str, sqlite3.Connection] = {}
        self._identities: dict[tuple[object, ...], sqlite3.Connection] = {}
        self._file_entries: list[
            tuple[Path, sqlite3.Connection, tuple[object, ...] | None]
        ] = []
        self._reserved = False
        self._target: sqlite3.Connection | None = None
        self._cleanup_errors: list[str] = []
        self._cleanup_done = False
        self._failed_path: Path | None = None

    @property
    def collab_conn(self) -> sqlite3.Connection:
        """入口打开的 collaboration 写连接（已 ``BEGIN IMMEDIATE``）。"""
        if self._collab_conn is None:
            raise RuntimeError("collaboration connection is not open")
        return self._collab_conn

    @property
    def connections(self) -> tuple[sqlite3.Connection, ...]:
        """全部保留连接，按获取顺序：collab、owner、其余规范排序输入。"""
        return tuple(self._connections)

    @property
    def target_conn(self) -> sqlite3.Connection | None:
        """已选择的目标连接；尚未 :meth:`select_target` 时为 ``None``。"""
        return self._target

    @property
    def cleanup_errors(self) -> tuple[str, ...]:
        """清理阶段的故障描述；成功提交后仍尽力清理并在此如实记录。"""
        return tuple(self._cleanup_errors)

    @property
    def failed_path(self) -> Path | None:
        """最近一次获取失败时正在处理的给定路径；成功获取不置值。

        只供上层在 ``reserve`` 异常后区分失败来源（owner/accounts/member
        等），不改变原异常的传播方式，也不代表该路径上的库本身不可读。
        """
        return self._failed_path

    def reserve(self, owner_path: Path, other_paths: Iterable[Path]) -> None:
        """打开 owner 与其余输入并保留写事务；整个窗口只允许一次。

        全部给定文件必须存在。物理去重覆盖 collab、owner 与硬链接别名；
        同一物理库只保留一个连接/事务，但全部已登记路径别名仍可经
        :meth:`connection` 取回。参数解析、排序或任何获取失败时回滚/关闭
        全部已打开连接并原样上抛；有效调用在参数迭代前即占用一次性名额，
        后续二次有效调用仍以 :class:`RuntimeError` 非破坏拒绝。
        """
        if self._cleanup_done:
            raise RuntimeError("reservation session is already closed")
        if self._reserved:
            raise RuntimeError("reserve() may be called only once")
        self._reserved = True
        try:
            if isinstance(other_paths, (str, bytes, os.PathLike)):
                raise TypeError(
                    "other_paths must be an iterable of paths, not a single path")
            owner = Path(owner_path)
            others = [Path(path) for path in other_paths]
            others.sort(key=_sort_key)
            self._reserve_one(owner)
            for path in others:
                self._reserve_one(path)
        except BaseException:
            self._cleanup()
            raise

    def connection(self, path: Path) -> sqlite3.Connection:
        """返回已登记来源的连接；绝不懒发现来源或打开新库。

        只按规范路径键查找；未在 :meth:`reserve` 中出现过的其他硬链接
        别名即使指向同一物理文件也拒绝。
        """
        if self._cleanup_done:
            raise RuntimeError("reservation session is already closed")
        conn = self._aliases.get(_path_key(Path(path)))
        if conn is None:
            raise RuntimeError(
                f"path is not a registered reservation source: {path}")
        return conn

    def select_target(self, path: Path) -> None:
        """在 ``reserve`` 完成后选择唯一目标；同一连接别名重复选择幂等。"""
        if self._cleanup_done:
            raise RuntimeError("reservation session is already closed")
        if not self._reserved:
            raise RuntimeError("select_target() requires reserve() first")
        conn = self._aliases.get(_path_key(Path(path)))
        if conn is None:
            raise RuntimeError(
                f"target is not a registered reservation source: {path}")
        if self._target is None:
            self._target = conn
        elif self._target is not conn:
            raise RuntimeError(
                "a different target connection is already selected; "
                "switching targets is not allowed")

    def _open_collab(self) -> None:
        try:
            conn = _open_reserved_connection(self._collab_path, self._timeout)
        except BaseException:
            self._failed_path = self._collab_path
            raise
        self._collab_conn = conn
        self._connections.append(conn)
        self._labels[id(conn)] = _path_key(self._collab_path)
        self._register_alias(self._collab_path, conn)

    def _reserve_one(self, path: Path) -> None:
        given = path
        try:
            path = Path(path)
            given = path
            if not path.is_file():
                raise FileNotFoundError(
                    f"reservation input must be an existing SQLite file: {path}")
            conn = self._registered_connection(path)
            if conn is None:
                conn = _open_reserved_connection(path, self._timeout)
                self._connections.append(conn)
                self._labels[id(conn)] = _path_key(path)
            self._register_alias(path, conn)
        except BaseException:
            if isinstance(given, Path):
                self._failed_path = given
            raise

    def _register_alias(self, path: Path, conn: sqlite3.Connection) -> None:
        key = _path_key(path)
        identity = _physical_identity(path)
        self._aliases[key] = conn
        if identity is not None:
            self._identities[identity] = conn
        self._file_entries.append((path, conn, identity))

    def _registered_connection(
        self, path: Path
    ) -> sqlite3.Connection | None:
        key = _path_key(path)
        conn = self._aliases.get(key)
        if conn is not None:
            return conn
        identity = _physical_identity(path)
        if identity is not None:
            conn = self._identities.get(identity)
            if conn is not None:
                self._aliases[key] = conn
                return conn
        # 仅有 inode 身份时不做 samefile 兜底：文件被替换后 samefile 可能把
        # 新文件错认成已登记旧连接。无 inode 身份（st_ino=0）时才用
        # samefile 补足硬链接去重。
        for registered_path, registered_conn, registered_identity in self._file_entries:
            if registered_identity is not None and registered_identity[0] == "inode":
                continue
            try:
                if path.samefile(registered_path):
                    self._aliases[key] = registered_conn
                    return registered_conn
            except OSError:
                continue
        return None

    def _commit_target(self) -> None:
        target = self._target
        if target is None:
            raise RuntimeError("no target connection selected")
        target.commit()

    def _cleanup(self, *, committed_target: bool = False) -> None:
        """回滚并关闭全部连接；绝不抛异常（清理失败只记录）。"""
        if self._cleanup_done:
            return
        self._cleanup_done = True
        connections = tuple(self._connections)
        committed = self._target if committed_target else None
        for conn in reversed(connections):
            if conn is committed:
                continue
            try:
                if conn.in_transaction:
                    conn.rollback()
            except BaseException as exc:
                self._cleanup_errors.append(
                    _describe_failure("rollback", self._label(conn), exc))
        for conn in reversed(connections):
            try:
                conn.close()
            except BaseException as exc:
                self._cleanup_errors.append(
                    _describe_failure("close", self._label(conn), exc))

    def _label(self, conn: sqlite3.Connection) -> str:
        return self._labels.get(id(conn), "<unknown connection>")


@contextmanager
def retained_write_connections(
    collab_path: Path, *, timeout: float = 15.0
) -> Iterator[ReservedWriteConnections]:
    """保留 collab、owner 与全部现存输入的写事务，单目标提交后释放。

    用法::

        with retained_write_connections(collab) as reserved:
            reserved.reserve(owner, other_paths)
            reserved.select_target(actor_path)
            ...  # 仅在这些连接上执行业务语句

    正常退出时只对所选目标执行一次 ``commit()``，随后其余输入
    ``rollback``、全部连接逆序 ``close``。业务异常、commit 失败或未选
    目标退出时不提交；清理故障不遮盖原异常。commit 成功后的清理故障
    只记录在 :attr:`ReservedWriteConnections.cleanup_errors`，不改变
    业务成功语义。
    """
    session = ReservedWriteConnections(collab_path, timeout=timeout)
    try:
        session._open_collab()
        yield session
    except BaseException:
        session._cleanup()
        raise

    if session._target is None:
        session._cleanup()
        raise RuntimeError(
            "retained write connections exited normally without a selected "
            "target; every reservation was rolled back and nothing committed")
    try:
        session._commit_target()
    except BaseException:
        session._cleanup()
        raise
    session._cleanup(committed_target=True)
