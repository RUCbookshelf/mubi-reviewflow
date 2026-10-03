"""Desktop data-directory migration, applied by the launcher before DB imports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

CONFIG_ENV = "REVIEWFLOW_STORAGE_CONFIG"
ENABLED_ENV = "REVIEWFLOW_STORAGE_ENABLED"
DATA_ENV = "COBOOKSHELF_DATA_DIR"
_MANIFEST_DIR = "migration-manifests"
_MANIFEST_VERSION = 1
_MIGRATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def config_path() -> Path:
    value = os.environ.get(CONFIG_ENV)
    if value:
        path = Path(value).expanduser()
        return Path(os.path.abspath(path))
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Preferences"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "ReviewFlow" / "local-storage.json"


def control_dir() -> Path:
    return config_path().parent


def lock_path() -> Path:
    return control_dir() / "storage.lock"


def is_enabled() -> bool:
    return os.environ.get(ENABLED_ENV) == "1" and os.environ.get("REVIEWFLOW_MODE") != "server"


def _read_config() -> dict:
    path = config_path()
    if not path.exists():
        return {"version": _MANIFEST_VERSION, "current_path": None, "pending": None,
                "last_error": None, "backup": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Storage settings could not be read: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") != _MANIFEST_VERSION:
        raise RuntimeError("Storage settings have an unsupported format.")
    value.setdefault("current_path", None)
    value.setdefault("pending", None)
    value.setdefault("last_error", None)
    value.setdefault("backup", None)
    return value


def _write_config(value: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".local-storage-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                return True
        except OSError:
            return True
    return False


def _excluded_paths(root: Path) -> set[str]:
    """Control files can sit in a user-selected data root; keep them out of moves."""
    control = _absolute(control_dir())
    root = _absolute(root)
    excludes: set[str] = set()
    if _inside(control, root) and control != root:
        excludes.add(control.relative_to(root).as_posix() + "/")
    # Windows bootstrap files share LOCALAPPDATA/ReviewFlow with user data.
    for name in ("reviewflow.log", "browser-launch.log", "startup_diagnostic.log", "runtime-deps"):
        candidate = root / name
        if candidate.exists():
            excludes.add(name + ("/" if candidate.is_dir() else ""))
    for candidate in root.glob("runtime-checked-*"):
        excludes.add(candidate.name + ("/" if candidate.is_dir() else ""))
    if excludes and control != root:
        return excludes
    if control == root:
        candidates = (config_path(), lock_path(), root / "desktop-port.txt", root / _MANIFEST_DIR)
        for path in candidates:
            try:
                rel = _absolute(path).relative_to(root).as_posix()
            except ValueError:
                continue
            excludes.add(rel + ("/" if path == root / _MANIFEST_DIR or path.is_dir() else ""))
    return excludes


def _is_excluded(relative: str, excludes: set[str]) -> bool:
    return any(relative == item.rstrip("/") or (item.endswith("/") and relative.startswith(item))
               for item in excludes)


def _walk_files(root: Path, excludes: set[str] | None = None):
    excludes = excludes or set()
    if not root.exists():
        return
    if not root.is_dir() or root.is_symlink():
        raise ValueError("The current data path must be a regular directory without symlinks.")
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=_raise_walk_error):
        base_path = Path(base)
        kept_dirs = []
        for name in dirs:
            path = base_path / name
            relative = path.relative_to(root).as_posix()
            if _is_excluded(relative, excludes):
                continue
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Data directories cannot contain symbolic links or junctions.")
            if not path.is_dir():
                raise ValueError("The data tree contains an unsupported file type.")
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            path = base_path / name
            relative = path.relative_to(root).as_posix()
            if _is_excluded(relative, excludes):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Data files cannot be symbolic links or special files.")
            yield relative, path


def _raise_walk_error(exc: OSError) -> None:
    raise exc


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_size(path: str | Path) -> int:
    root = _absolute(path)
    return sum(item.stat().st_size for _, item in _walk_files(root, _excluded_paths(root)))


def _manifest(root: Path, excludes: set[str] | None = None) -> list[dict]:
    excludes = excludes or set()
    rows = []
    if not root.exists():
        return rows
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=_raise_walk_error):
        base_path = Path(base)
        rel_base = base_path.relative_to(root)
        kept_dirs = []
        for name in dirs:
            path = base_path / name
            relative = (rel_base / name).as_posix()
            if _is_excluded(relative, excludes):
                continue
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Data directories cannot contain symbolic links or junctions.")
            rows.append({"path": relative, "type": "directory"})
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            path = base_path / name
            relative = (rel_base / name).as_posix()
            if _is_excluded(relative, excludes):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Data files cannot be symbolic links or special files.")
            rows.append({"path": relative, "type": "file", "size": path.stat().st_size,
                         "sha256": _file_hash(path)})
    return sorted(rows, key=lambda row: (row["path"], row["type"]))


def _manifest_hash(rows: list[dict]) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _validate_tree_paths(source: Path, destination: Path, *, require_empty: bool = True) -> tuple[int, int]:
    source = _absolute(source)
    destination = _absolute(destination)
    if not destination.is_absolute():
        raise ValueError("Choose an absolute folder path.")
    if source == destination or _inside(source, destination) or _inside(destination, source):
        raise ValueError("The destination must be separate from the current data folder.")
    if _has_symlink(source) or _has_symlink(destination):
        raise ValueError("Data folders cannot use symbolic links or junctions.")
    if not source.exists() or not source.is_dir():
        raise ValueError("The current data folder is missing or is not a directory.")
    if not destination.parent.exists() or not destination.parent.is_dir():
        raise ValueError("The destination's parent folder must already exist.")
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("The destination must be an empty folder.")
        if require_empty and any(destination.iterdir()):
            raise ValueError("The destination folder must be empty.")
    # Inspect every source entry before accepting the request; copytree must not follow links.
    source_files = list(_walk_files(source, _excluded_paths(source)))
    size = sum(path.stat().st_size for _, path in source_files)
    db_bytes = sum(path.stat().st_size for _, path in source_files
                   if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"})
    required = size + db_bytes  # copied files plus a rollback journal while rebasing paths
    if _free_bytes(destination if destination.exists() else destination.parent) < required:
        raise ValueError("The destination volume does not have enough free space.")
    try:
        fd, probe = tempfile.mkstemp(prefix=".reviewflow-write-check-", dir=destination.parent)
        os.close(fd)
        Path(probe).unlink()
    except OSError as exc:
        raise ValueError("The destination parent folder is not writable.") from exc
    return size, required


def schedule_migration(source: str | Path, destination: str) -> dict:
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("Choose an absolute destination folder.")
    if not Path(destination).expanduser().is_absolute():
        raise ValueError("Choose an absolute destination folder.")
    value = _read_config()
    active = _absolute(source)
    target = _absolute(destination)
    size, required = _validate_tree_paths(active, target)
    existing = value.get("pending")
    if existing and _absolute(existing["destination"]) != target:
        raise ValueError("Another data-folder migration is already queued. Cancel it first.")
    pending = existing or {"id": uuid.uuid4().hex}
    pending.update(source=str(active), destination=str(target), bytes=size, required_bytes=required)
    value.update(current_path=str(active), pending=pending, last_error=None)
    _write_config(value)
    return state(active, enabled=True)


def cancel_migration() -> dict:
    value = _read_config()
    value.update(pending=None, last_error=None)
    _write_config(value)
    return value


def initialize_data_dir(default: str | Path, *, prefer_config: bool = True) -> Path:
    """Resolve the active path and initialize control state before backend imports."""
    value = _read_config()
    previously_configured = bool(value.get("current_path"))
    explicit_data = os.environ.get(DATA_ENV)
    if prefer_config and value.get("current_path"):
        current = _absolute(value["current_path"])
    elif explicit_data:
        current = _absolute(explicit_data)
    else:
        current = _absolute(default)
    if value.get("current_path") is None:
        value["current_path"] = str(current)
        _write_config(value)
    pending = value.get("pending")
    if pending:
        try:
            current = _apply_pending(value)
        except Exception as exc:  # visible in Settings, and the old path stays active
            value = _read_config()
            value["last_error"] = str(exc)[:1000]
            _write_config(value)
            current = _absolute(value.get("current_path") or current)
    elif not previously_configured:
        current.mkdir(parents=True, exist_ok=True)
    if not current.is_dir():
        raise RuntimeError(f"The configured data folder is unavailable: {current}")
    return current


def _manifest_file(migration_id: str) -> Path:
    return control_dir() / _MANIFEST_DIR / f"{migration_id}.json"


def _stage_marker_file(migration_id: str) -> Path:
    return control_dir() / _MANIFEST_DIR / f"stage-{migration_id}.json"


def _stage_is_owned(path: Path, marker_path: Path, pending: dict) -> bool:
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        return (marker.get("id") == pending.get("id")
                and _absolute(marker.get("source", "")) == _absolute(pending.get("source", ""))
                and _absolute(marker.get("destination", "")) == _absolute(pending.get("destination", ""))
                and path.name == f".{Path(pending['destination']).name}.reviewflow-{pending['id']}.tmp")
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _write_manifest_file(path: Path, source: Path, rows: list[dict], excludes: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": _MANIFEST_VERSION, "source": str(source), "entries": rows,
            "excluded": sorted(excludes)}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)


def _copytree(source: Path, target: Path, excludes: set[str]) -> None:
    target.mkdir(parents=True)
    for base, dirs, files in os.walk(source, topdown=True, followlinks=False, onerror=_raise_walk_error):
        base_path = Path(base)
        rel_base = base_path.relative_to(source)
        dest_base = target / rel_base
        for name in list(dirs):
            rel = (rel_base / name).as_posix()
            path = base_path / name
            if _is_excluded(rel, excludes):
                dirs.remove(name)
                continue
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Data directories cannot contain symbolic links or junctions.")
            (dest_base / name).mkdir(exist_ok=True)
        for name in files:
            path = base_path / name
            rel = (rel_base / name).as_posix()
            if _is_excluded(rel, excludes):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Data files cannot be symbolic links or special files.")
            shutil.copy2(path, dest_base / name)


def _rebase_db_paths(path: Path, source: Path, destination: Path, copied_root: Path) -> None:
    uri = path.resolve().as_uri() + "?mode=rw"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise ValueError(f"A copied database could not be opened: {path.name}") from exc
    try:
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.Error as exc:
            raise ValueError(f"A copied database failed integrity verification: {path.name}") from exc
        if integrity != "ok":
            raise ValueError(f"A copied database failed integrity verification: {path.name}")
        updates = []
        for table in ("fulltexts", "coding_note_images"):
            if table not in tables:
                continue
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "path" not in columns:
                continue
            for rowid, raw in conn.execute(f"SELECT rowid,path FROM {table}"):
                candidate = Path(str(raw))
                if not candidate.is_absolute():
                    continue
                candidate = _absolute(candidate)
                if _inside(candidate, source):
                    relative = candidate.relative_to(source)
                    if candidate.is_file() and not (copied_root / relative).is_file():
                        raise ValueError(f"A copied attachment is missing: {relative}")
                    rebased = destination / relative
                    updates.append((table, int(rowid), str(rebased)))
        if updates:
            with conn:
                for table, rowid, rebased in updates:
                    conn.execute(f"UPDATE {table} SET path=? WHERE rowid=?", (rebased, rowid))
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"A copied database failed integrity verification: {path.name}")
    finally:
        conn.close()


def _save_commit_marker(path: Path, pending: dict, source_manifest: list[dict], target_manifest: list[dict], manifest_path: Path, excludes: set[str]) -> None:
    marker = {"id": pending["id"], "source": pending["source"],
              "destination": pending["destination"],
              "source_manifest": str(manifest_path),
              "target_hash": _manifest_hash(target_manifest),
              "target_entries": len(target_manifest), "excluded": sorted(excludes)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _commit_migration(value: dict, pending: dict, manifest_path: Path) -> Path:
    source = _absolute(pending["source"])
    destination = _absolute(pending["destination"])
    value.update(current_path=str(destination), pending=None, last_error=None,
                 backup={"path": str(source), "manifest": str(manifest_path),
                         "completed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    _write_config(value)
    try:
        (control_dir() / f"migration-ready-{pending['id']}.json").unlink(missing_ok=True)
        _stage_marker_file(pending["id"]).unlink(missing_ok=True)
    except OSError:
        pass
    return destination


def _apply_pending(value: dict) -> Path:
    pending = dict(value["pending"])
    if not _MIGRATION_ID_RE.fullmatch(str(pending.get("id", ""))):
        raise ValueError("The queued migration identifier is invalid.")
    source, destination = _absolute(pending["source"]), _absolute(pending["destination"])
    configured = _absolute(value.get("current_path") or source)
    if source != configured:
        raise ValueError("Queued migration no longer matches the active data folder.")
    marker_path = control_dir() / f"migration-ready-{pending['id']}.json"
    stage = destination.parent / f".{destination.name}.reviewflow-{pending['id']}.tmp"
    stage_marker = _stage_marker_file(pending["id"])
    if destination.exists() and marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if (marker.get("id") != pending["id"]
                or _absolute(marker.get("source", "")) != source
                or _absolute(marker.get("destination", "")) != destination):
            raise ValueError("A prepared migration does not match the queued destination.")
        current_manifest = _manifest(destination, set(marker.get("excluded", [])))
        if (_manifest_hash(current_manifest) != marker.get("target_hash")
                or len(current_manifest) != marker.get("target_entries")):
            raise ValueError("The prepared destination changed after migration; it was left untouched.")
        manifest_path = _manifest_file(pending["id"])
        if Path(marker.get("source_manifest", "")).resolve() != manifest_path.resolve():
            raise ValueError("A prepared migration manifest path is invalid.")
        return _commit_migration(value, pending, manifest_path)

    _validate_tree_paths(source, destination)
    if stage.exists():
        if not _stage_is_owned(stage, stage_marker, pending):
            raise ValueError("An unrelated folder occupies the migration staging path; it was left untouched.")
        shutil.rmtree(stage)
        stage_marker.unlink(missing_ok=True)
    excludes = _excluded_paths(source)
    source_manifest = _manifest(source, excludes)
    manifest_path = _manifest_file(pending["id"])
    _write_manifest_file(manifest_path, source, source_manifest, excludes)
    stage_marker.parent.mkdir(parents=True, exist_ok=True)
    stage_marker.write_text(json.dumps({"id": pending["id"], "source": str(source),
                                        "destination": str(destination)}), encoding="utf-8")
    try:
        _copytree(source, stage, excludes)
        copied = _manifest(stage)
        if copied != source_manifest:
            raise ValueError("Copied data failed file-hash verification.")
        for relative, _ in _walk_files(stage):
            path = stage / relative
            if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                # Paths must reference the final folder; stage is only the copy location.
                _rebase_db_paths(path, source, destination, stage)
        final_manifest = _manifest(stage)
        _save_commit_marker(marker_path, pending, source_manifest, final_manifest, manifest_path, excludes)
        if destination.exists():
            if any(destination.iterdir()):
                raise ValueError("The destination folder is no longer empty.")
            destination.rmdir()
        os.replace(stage, destination)
    except BaseException:
        if stage.exists() and _stage_is_owned(stage, stage_marker, pending):
            shutil.rmtree(stage, ignore_errors=True)
            stage_marker.unlink(missing_ok=True)
        raise
    return _commit_migration(value, pending, manifest_path)


def state(current: str | Path, *, enabled: bool | None = None) -> dict:
    feature_enabled = is_enabled() if enabled is None else enabled
    value = _read_config() if feature_enabled else {}
    active = _absolute((value.get("current_path") or current) if feature_enabled else current)
    pending = (value.get("pending") or {}) if feature_enabled else {}
    try:
        size = tree_size(active) if active.exists() else 0
    except (OSError, ValueError):
        size = None
    try:
        free = _free_bytes(active)
    except OSError:
        free = None
    return {"enabled": feature_enabled,
            "current_path": str(active),
            "pending_path": pending.get("destination"),
            "migration_pending": bool(pending),
            "last_error": value.get("last_error") if feature_enabled else None,
            "backup_path": (value.get("backup") or {}).get("path"),
            "bytes": size,
            "free_bytes": free,
            "can_browse": sys.platform == "win32" and shutil.which("powershell.exe") is not None}


def browse_folder() -> str | None:
    if sys.platform != "win32" or not shutil.which("powershell.exe"):
        raise RuntimeError("Folder browsing is available from the Windows desktop app only.")
    script = ("Add-Type -AssemblyName System.Windows.Forms; "
              "$d=New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$d.Description='Choose a data folder'; $d.ShowNewFolderButton=$true; "
              "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
              "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Write($d.SelectedPath)}")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The Windows folder picker timed out.") from exc
    if result.returncode:
        raise RuntimeError("The Windows folder picker could not be opened.")
    value = result.stdout.decode("utf-8", errors="replace").strip()
    return value or None


def cleanup_backup(confirm: bool) -> dict:
    if confirm is not True:
        raise ValueError("Explicit confirmation is required to remove the old data folder.")
    value = _read_config()
    backup = value.get("backup")
    if not isinstance(backup, dict) or not backup.get("path") or not backup.get("manifest"):
        raise ValueError("There is no migrated backup folder to clean up.")
    root = _absolute(backup["path"])
    active = _absolute(value.get("current_path") or "")
    if root == active or _inside(root, active) or _inside(active, root):
        raise ValueError("The backup folder overlaps the active data folder and cannot be removed.")
    manifest_path = _absolute(backup["manifest"])
    if _has_symlink(root) or _has_symlink(manifest_path):
        raise ValueError("The backup contains a symbolic link and was left untouched.")
    if manifest_path.parent != _absolute(control_dir() / _MANIFEST_DIR):
        raise ValueError("The backup manifest location is invalid; the folder was left untouched.")
    try:
        body = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("The backup manifest is invalid; the folder was left untouched.") from exc
    if (not isinstance(body, dict) or body.get("version") != _MANIFEST_VERSION
            or _absolute(body.get("source", "")) != root):
        raise ValueError("The backup manifest does not match this folder; it was left untouched.")
    expected = body.get("entries")
    if not isinstance(expected, list):
        raise ValueError("The backup manifest is invalid; the folder was left untouched.")
    excludes = set(body.get("excluded", [])) | _excluded_paths(root)
    actual = _manifest(root, excludes)
    if actual != expected:
        raise ValueError("The old folder changed after migration. It was left untouched.")
    # Remove only file entries whose content still matches the migration manifest.
    remove_paths, remove_dirs = [], []
    for row in expected:
        if not isinstance(row, dict):
            raise ValueError("The backup manifest is invalid; the folder was left untouched.")
        relative = PurePosixPath(row.get("path", ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("The backup manifest contains an unsafe path; the folder was left untouched.")
        file_path = root.joinpath(*relative.parts)
        if not _inside(_absolute(file_path), root):
            raise ValueError("The backup manifest contains an unsafe path; the folder was left untouched.")
        if row.get("type") == "file":
            digest = _file_hash(file_path)
            if digest != row.get("sha256"):
                raise ValueError("The old folder changed during cleanup. It was left untouched.")
            remove_paths.append((file_path, row["sha256"]))
        elif row.get("type") == "directory" and file_path.is_dir():
            remove_dirs.append((len(relative.parts), file_path))
        else:
            raise ValueError("The backup manifest is invalid; the folder was left untouched.")
    for file_path, expected_hash in remove_paths:
        if _file_hash(file_path) != expected_hash:
            raise ValueError("The old folder changed during cleanup. It was left untouched.")
        file_path.unlink()
    for _depth, path in sorted(remove_dirs, reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    preserves_control = any((root / item.rstrip("/")).exists() for item in excludes)
    if not preserves_control:
        try:
            root.rmdir()
        except OSError:
            pass
    value["backup"] = None
    _write_config(value)
    manifest_path.unlink(missing_ok=True)
    return state(active, enabled=True)
