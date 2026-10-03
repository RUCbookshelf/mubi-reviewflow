"""Authenticated local-only controls for desktop data-folder migration."""
from __future__ import annotations

import ipaddress
import os
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from coscreen import local_storage


class MigrationIn(BaseModel):
    destination: str = Field(min_length=1, max_length=4096)


class CleanupIn(BaseModel):
    confirm: StrictBool


def _check_local_desktop(request: Request) -> None:
    if os.environ.get("REVIEWFLOW_MODE") == "server":
        raise HTTPException(403, "Local storage settings are available in the desktop app only.")
    host = request.client.host if request.client else ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    if not local:
        raise HTTPException(403, "Local storage settings accept requests from this computer only.")


def register_local_storage_routes(
    app: FastAPI,
    require_user: Callable[..., dict],
    data_dir,
) -> None:
    """Register routes after the app's standard authentication dependency exists."""

    @app.get("/api/local-storage")
    def get_local_storage(request: Request, _user: dict = Depends(require_user)) -> dict:
        _check_local_desktop(request)
        return local_storage.state(data_dir)

    @app.post("/api/local-storage/migration")
    def queue_migration(body: MigrationIn, request: Request,
                        _user: dict = Depends(require_user)) -> dict:
        _check_local_desktop(request)
        if not local_storage.is_enabled():
            raise HTTPException(403, "Data-folder migration is disabled for this launch configuration.")
        if not os.path.isabs(body.destination):
            raise HTTPException(400, "Choose an absolute destination folder.")
        try:
            return local_storage.schedule_migration(data_dir, body.destination)
        except ValueError as exc:
            status = 409 if "already queued" in str(exc) else 400
            raise HTTPException(status, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, f"The destination could not be checked: {exc}") from exc

    @app.post("/api/local-storage/migration/cancel")
    def cancel_migration(request: Request,
                         _user: dict = Depends(require_user)) -> dict:
        _check_local_desktop(request)
        if not local_storage.is_enabled():
            raise HTTPException(403, "Data-folder migration is disabled for this launch configuration.")
        return local_storage.cancel_migration()

    @app.post("/api/local-storage/browse")
    def browse_folder(request: Request, _user: dict = Depends(require_user)) -> dict:
        _check_local_desktop(request)
        if not local_storage.is_enabled():
            raise HTTPException(403, "Data-folder migration is disabled for this launch configuration.")
        try:
            return {"path": local_storage.browse_folder()}
        except RuntimeError as exc:
            raise HTTPException(501, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, "The native folder picker could not be opened.") from exc

    @app.post("/api/local-storage/cleanup")
    def cleanup_backup(body: CleanupIn, request: Request,
                       _user: dict = Depends(require_user)) -> dict:
        _check_local_desktop(request)
        if not local_storage.is_enabled():
            raise HTTPException(403, "Data-folder cleanup is disabled for this launch configuration.")
        try:
            return local_storage.cleanup_backup(body.confirm)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(409, f"The backup could not be cleaned up safely: {exc}") from exc
