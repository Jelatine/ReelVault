"""Read-only, independently authenticated WebDAV view of the video catalog."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import mimetypes
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as safe_xml
from defusedxml.common import DefusedXmlException
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from .auth import hash_token, require_auth
from .config import Settings
from .db import get_db
from .errors import APIError
from .library import abs_path
from .locations import LocationUnavailable
from .models import Collection, CollectionItem, Folder, RuntimeSetting, Video
from .storage import budget_transaction

DAV = "{DAV:}"
USERNAME = "reelvault"
ALLOW = "OPTIONS, PROPFIND, GET, HEAD"
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PROPERTIES = (
    DAV + "displayname",
    DAV + "resourcetype",
    DAV + "creationdate",
    DAV + "getlastmodified",
    DAV + "getcontentlength",
    DAV + "getcontenttype",
    DAV + "getetag",
    DAV + "supportedlock",
    DAV + "lockdiscovery",
)
router = APIRouter(tags=["webdav"])
settings_router = APIRouter(
    prefix="/api/system/webdav", tags=["webdav"], dependencies=[Depends(require_auth)]
)


def xml_response(element: ET.Element, status: int = 207) -> Response:
    return Response(
        ET.tostring(element, encoding="utf-8", xml_declaration=True),
        status_code=status,
        media_type="application/xml",
        headers=HEADERS,
    )


def error(status: int, condition: str | None = None) -> Response:
    root = ET.Element(DAV + "error")
    if condition:
        ET.SubElement(root, DAV + condition)
    response = xml_response(root, status)
    if status == 405:
        response.headers["Allow"] = ALLOW
    return response


def clean_name(name: str) -> str:
    # Names are virtual catalog labels, never filesystem paths.
    return re.sub(r"[/\\\x00-\x1f\x7f]", "_", name)[:160].strip() or "video"


@dataclass
class Entry:
    path: str
    name: str
    created: datetime = EPOCH
    modified: datetime = EPOCH
    file: str | None = None
    size: int = 0
    mime: str = "httpd/unix-directory"
    etag: str = ""

    @property
    def directory(self) -> bool:
        return self.file is None


def visible_videos():
    return (
        select(Video)
        .options(lazyload(Video.tags))
        .where(Video.status == "ready", Video.deleted_at.is_(None))
    )


def video_entry(settings: Settings, parent: str, video: Video) -> Entry:
    suffix = Path(video.file_path).suffix.lower()
    name = clean_name(video.title) + suffix
    return Entry(
        parent + video.id + "--" + name,
        name,
        video.created_at,
        video.updated_at,
        video.file_path,
        video.size,
        mimetypes.guess_type(name)[0] or "application/octet-stream",
        '"'
        + hashlib.sha256(
            f"{video.id}:{video.file_path}:{video.size}:{video.asset_version}".encode()
        ).hexdigest()
        + '"',
    )


def catalog(
    db: Session, settings: Settings, value: str, *, include_children: bool = True
) -> tuple[Entry, list[Entry]]:
    """Resolve virtual IDs, validating folder ancestry and collection membership."""
    parts = value.strip("/").split("/") if value.strip("/") else []
    if len(parts) > 34 or any(part in {"", ".", ".."} for part in parts):
        raise LookupError
    path = "/dav/" + "/".join(parts)
    path = path.rstrip("/") + "/"
    node = Entry(path, "ReelVault")
    if not parts:
        return node, [
            Entry("/dav/all/", "All videos"),
            Entry("/dav/folders/", "Folders"),
            Entry("/dav/collections/", "Collections"),
        ]
    kind = parts[0]
    if kind not in {"all", "folders", "collections"}:
        raise LookupError
    videos = visible_videos()
    folders = []
    collections = []
    if kind == "all":
        if len(parts) > 2:
            raise LookupError
        node.name = "All videos"
    elif kind == "folders":
        parent_id = None
        for index, part in enumerate(parts[1:], start=1):
            if re.fullmatch(r"[a-f0-9]{32}--.+", part):
                if index != len(parts) - 1:
                    raise LookupError
                break
            identifier = part.split("--", 1)[0]
            if not re.fullmatch(r"[1-9][0-9]{0,18}", identifier):
                raise LookupError
            folder = db.get(Folder, int(identifier))
            if folder is None or folder.parent_id != parent_id:
                raise LookupError
            parent_id = folder.id
            node.name, node.created = folder.name, folder.created_at
        videos = videos.where(Video.folder_id == parent_id)
        if include_children:
            folders = list(db.scalars(select(Folder).where(Folder.parent_id == parent_id)))
        if len(parts) == 1:
            node.name = "Folders"
    else:
        node.name = "Collections"
        if len(parts) == 1:
            if include_children:
                collections = list(
                    db.scalars(select(Collection).order_by(Collection.name, Collection.id))
                )
            videos = videos.where(False)
        else:
            identifier = parts[1].split("--", 1)[0]
            if len(parts) > 3 or not re.fullmatch(r"[1-9][0-9]{0,18}", identifier):
                raise LookupError
            collection = db.get(Collection, int(identifier))
            if collection is None:
                raise LookupError
            node.name, node.created = collection.name, collection.created_at
            videos = videos.join(CollectionItem).where(
                CollectionItem.collection_id == collection.id
            )
            videos = videos.order_by(CollectionItem.position)
    leaf = parts[-1]
    if re.fullmatch(r"[a-f0-9]{32}--.+", leaf):
        video = db.scalar(videos.where(Video.id == leaf.split("--", 1)[0]))
        if video is None:
            raise LookupError
        entry = video_entry(settings, path.rsplit("/", 2)[0] + "/", video)
        entry.path = path.rstrip("/")  # Keep old labeled URLs working after renaming.
        return entry, []
    if kind == "all" and len(parts) != 1:
        raise LookupError
    if not include_children:
        return node, []
    entries = [
        Entry(path + f"{folder.id}--{clean_name(folder.name)}/", folder.name, folder.created_at)
        for folder in sorted(folders, key=lambda f: (f.name, f.id))
    ]
    entries += [
        Entry(
            path + f"{collection.id}--{clean_name(collection.name)}/",
            collection.name,
            collection.created_at,
        )
        for collection in collections
    ]
    entries += [
        video_entry(settings, path, video)
        for video in db.scalars(videos.order_by(Video.title, Video.id))
    ]
    return node, entries


async def properties(request: Request) -> tuple[str, list[str]]:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise ValueError
    if not data:
        return "allprop", list(PROPERTIES)
    root = safe_xml.fromstring(bytes(data), forbid_dtd=True)
    if root.tag != DAV + "propfind" or sum(1 for _ in root.iter()) > 256:
        raise ValueError
    selectors = [
        child for child in root if child.tag in {DAV + "allprop", DAV + "propname", DAV + "prop"}
    ]
    if len(selectors) != 1:
        raise ValueError
    selector = selectors[0]
    if selector.tag == DAV + "prop":
        return "prop", list(dict.fromkeys(child.tag for child in selector))
    extra = root.find(DAV + "include")
    return selector.tag[len(DAV) :], list(
        dict.fromkeys([*PROPERTIES, *([] if extra is None else [child.tag for child in extra])])
    )


def multistatus(entries: list[Entry], mode: str, requested: list[str], root_path: str) -> Response:
    root = ET.Element(DAV + "multistatus")
    for entry in entries:
        response = ET.SubElement(root, DAV + "response")
        ET.SubElement(response, DAV + "href").text = quote(root_path + entry.path, safe="/")
        known = [name for name in requested if name in PROPERTIES]
        unknown = [name for name in requested if name not in PROPERTIES]
        for names, status in ((known, "200 OK"), (unknown, "404 Not Found")):
            if not names:
                continue
            propstat = ET.SubElement(response, DAV + "propstat")
            prop = ET.SubElement(propstat, DAV + "prop")
            for name in names:
                element = ET.SubElement(prop, name)
                if name in unknown or mode == "propname":
                    continue
                if name == DAV + "resourcetype" and entry.directory:
                    ET.SubElement(element, DAV + "collection")
                else:
                    element.text = {
                        DAV + "displayname": clean_name(entry.name),
                        DAV + "creationdate": entry.created.astimezone(UTC)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        DAV + "getlastmodified": format_datetime(
                            entry.modified.astimezone(UTC), usegmt=True
                        ),
                        DAV + "getcontentlength": str(entry.size),
                        DAV + "getcontenttype": entry.mime,
                        DAV + "getetag": entry.etag,
                    }.get(name)
            ET.SubElement(propstat, DAV + "status").text = "HTTP/1.1 " + status
    return xml_response(root)


def credentials(db: Session, settings: Settings) -> tuple[bool, str]:
    saved = db.get(RuntimeSetting, "webdav", populate_existing=True)
    if saved is None:
        return settings.webdav_enabled, settings.webdav_token_hash
    return bool(saved.value["webdav_enabled"]), str(saved.value["webdav_token_hash"])


def authenticate(request: Request, settings: Settings) -> Response | None:
    with request.app.state.sessionmaker() as db:
        enabled, token_hash = credentials(db, settings)
    if not enabled or not token_hash:
        return error(404)
    authorization = request.headers.get("authorization", "")
    try:
        scheme, encoded = authorization.split(" ", 1)
        if scheme.lower() != "basic" or len(encoded) > 1024:
            raise ValueError
        user, password = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
        valid = hmac.compare_digest(user.encode(), USERNAME.encode()) & hmac.compare_digest(
            hash_token(password), token_hash
        )
    except (ValueError, binascii.Error, UnicodeError):
        valid = False
    if valid:
        return None
    return Response(
        status_code=401,
        headers={**HEADERS, "WWW-Authenticate": 'Basic realm="ReelVault WebDAV", charset="UTF-8"'},
    )


@router.api_route(
    "/dav",
    methods=[
        "OPTIONS",
        "PROPFIND",
        "GET",
        "HEAD",
        "PUT",
        "DELETE",
        "POST",
        "PATCH",
        "MKCOL",
        "COPY",
        "MOVE",
        "LOCK",
        "UNLOCK",
        "PROPPATCH",
    ],
)
@router.api_route(
    "/dav/{value:path}",
    methods=[
        "OPTIONS",
        "PROPFIND",
        "GET",
        "HEAD",
        "PUT",
        "DELETE",
        "POST",
        "PATCH",
        "MKCOL",
        "COPY",
        "MOVE",
        "LOCK",
        "UNLOCK",
        "PROPPATCH",
    ],
)
async def browse(request: Request, value: str = "") -> Response:
    settings: Settings = request.app.state.settings
    denied = await run_in_threadpool(authenticate, request, settings)
    if denied is not None:
        return denied
    if request.method == "OPTIONS":
        return Response(status_code=200, headers={**HEADERS, "DAV": "1", "Allow": ALLOW})
    if request.method not in {"GET", "HEAD", "PROPFIND"}:
        return Response(status_code=405, headers={**HEADERS, "Allow": ALLOW})

    def lookup():
        with request.app.state.sessionmaker() as db:
            return catalog(
                db,
                settings,
                value,
                include_children=request.method == "PROPFIND"
                and request.headers.get("depth") == "1",
            )

    try:
        entry, children = await run_in_threadpool(lookup)
    except LookupError:
        return error(404)
    except (ValueError, OSError):
        return error(503)
    if request.method == "PROPFIND":
        depth = request.headers.get("depth", "infinity")
        if depth == "infinity":
            if entry.directory:
                return error(403, "propfind-finite-depth")
            depth = "0"
        if depth not in {"0", "1"}:
            return error(400)
        try:
            mode, requested = await properties(request)
        except (ValueError, UnicodeError, ET.ParseError, DefusedXmlException):
            return error(400)
        return multistatus(
            [entry, *(children if depth == "1" else [])],
            mode,
            requested,
            request.scope.get("root_path", ""),
        )
    if entry.file is None:
        return error(405)
    try:
        path = await run_in_threadpool(abs_path, settings, entry.file)
        stat = await run_in_threadpool(path.stat)
        if not await run_in_threadpool(path.is_file):
            return error(404)
    except (ValueError, APIError):
        return error(503)
    except LocationUnavailable:
        return error(503)
    except OSError:
        return error(404)
    return FileResponse(
        path, media_type=entry.mime, stat_result=stat, headers={**HEADERS, "ETag": entry.etag}
    )


def status(settings: Settings, db: Session) -> dict[str, Any]:
    enabled, token_hash = credentials(db, settings)
    return {
        "enabled": enabled,
        "credential_configured": bool(token_hash),
        "username": USERNAME,
        "path": "/dav/",
    }


def persist(db: Session, settings: Settings, enabled: bool, token_hash: str) -> None:
    db.merge(
        RuntimeSetting(
            key="webdav", value={"webdav_enabled": enabled, "webdav_token_hash": token_hash}
        )
    )
    db.commit()
    settings.webdav_enabled, settings.webdav_token_hash = enabled, token_hash


@settings_router.get("")
def get_status(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    return status(request.app.state.settings, db)


class EnabledBody(BaseModel):
    enabled: bool


@settings_router.put("")
def configure(body: EnabledBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = request.app.state.settings
    with budget_transaction(db):
        _, token_hash = credentials(db, settings)
        if body.enabled and not token_hash:
            raise APIError(409, "请先生成播放器访问密码", code="conflict")
        persist(db, settings, body.enabled, token_hash)
    return status(settings, db)


@settings_router.post("/credential")
def rotate(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    password = secrets.token_urlsafe(32)
    with budget_transaction(db):
        enabled, _ = credentials(db, settings)
        persist(db, settings, enabled, hash_token(password))
    return {**status(settings, db), "password": password}


@settings_router.delete("/credential")
def revoke(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = request.app.state.settings
    with budget_transaction(db):
        persist(db, settings, False, "")
    return status(settings, db)
