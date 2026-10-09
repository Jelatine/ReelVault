"""Stable local library locations with removable-drive identity checks.

The primary library keeps historical relative paths. Additional locations use
volumes/<stable-id>/<relative-file>, so changing a mount path doesn't rewrite
video history, job source versions or duplicate fingerprints.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath

from .config import Settings, StorageRoot
from .fsutil import NONBLOCK, open_nofollow

MARKER = ".reelvault-location.json"
ID = re.compile(r"^[a-f0-9]{32}$")


class LocationUnavailable(OSError):
    """Also an OSError so retention never removes records for an offline drive."""

    def __init__(self, location_id: str, reason: str = "存储位置未连接或标识不匹配"):
        self.location_id = location_id
        super().__init__(reason)


def _read_identity(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise OSError("存储目录不可用")
    descriptor = open_nofollow(root / MARKER, os.O_RDONLY | NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
            raise OSError("存储标识文件无效")
        value = json.loads(os.read(descriptor, 1025))
        if (
            set(value) != {"version", "id"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or not ID.fullmatch(value["id"])
        ):
            raise OSError("存储标识文件无效")
        return str(value["id"])
    except (ValueError, TypeError, KeyError) as error:
        raise OSError("存储标识文件无效") from error
    finally:
        os.close(descriptor)


def library_root(settings: Settings, location_id: str = "local") -> Path:
    if location_id == "local":
        return settings.library_dir
    if location_id == "s3":
        from .object_library import bound_config

        namespace_id = settings.s3_current
        if namespace_id is None:
            raise LocationUnavailable("s3")
        bound_config(settings, namespace_id)
        root = settings.data_dir / "objects"
        cache = root / namespace_id
        if root.is_symlink() or cache.is_symlink():
            raise LocationUnavailable("s3")
        root.mkdir(mode=0o700, exist_ok=True)
        cache.mkdir(mode=0o700, exist_ok=True)
        return cache
    if not ID.fullmatch(location_id) or location_id not in settings.storage_locations:
        raise LocationUnavailable(location_id)
    root = settings.storage_locations[location_id].path
    try:
        if _read_identity(root) != location_id:
            raise OSError("存储位置标识不匹配")
        library = root / "library"
        if library.is_symlink() or not library.is_dir():
            raise OSError("视频库目录不可用")
        return library
    except OSError as error:
        raise LocationUnavailable(location_id) from error


def register_root(settings: Settings, path: Path, name: str) -> tuple[str, StorageRoot]:
    """Initialize an existing empty directory or reconnect an identified library.

    Never mkdir the selected root: a disconnected mount must not silently become
    a directory on the system disk. Only our child library and marker are created.
    """
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise OSError("请选择已存在的绝对目录")
    path = path.resolve(strict=True)
    name = name.strip()
    if not name or len(name) > 128:
        raise ValueError("存储位置名称无效")
    roots = [
        settings.data_dir.resolve(),
        *(entry.path for entry in settings.storage_locations.values()),
    ]
    for existing in roots:
        if path == existing or path.is_relative_to(existing) or existing.is_relative_to(path):
            raise ValueError("存储目录不能重叠或包含主数据目录")
    marker = path / MARKER
    if marker.exists() or marker.is_symlink():
        location_id = _read_identity(path)
        library = path / "library"
        if library.is_symlink() or not library.is_dir():
            raise OSError("视频库目录不可用")
    else:
        if any(path.iterdir()):
            raise ValueError("新存储位置必须是空目录，已有视频请使用目录导入")
        location_id = uuid.uuid4().hex
        library = path / "library"
        library.mkdir()
        created_marker = False
        try:
            descriptor = open_nofollow(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_marker = True
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"version": 1, "id": location_id}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            if created_marker:
                with contextlib.suppress(OSError):
                    marker.unlink()
            with contextlib.suppress(OSError):
                library.rmdir()
            raise
    if location_id in settings.storage_locations:
        raise ValueError("该存储位置已登记，请更新挂载路径")
    return location_id, StorageRoot(name=name, path=path)


def relative_parts(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or "\x00" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError("非法媒体路径")
    return path.parts


def location_of(value: str) -> str:
    parts = relative_parts(value)
    if parts[0] == "s3":
        from .object_types import KEY

        if len(parts) != 3 or not ID.fullmatch(parts[1]) or not KEY.fullmatch(parts[2]):
            raise ValueError("非法对象存储路径")
        return "s3"
    if parts[0] != "volumes":
        return "local"
    if len(parts) < 3 or not ID.fullmatch(parts[1]):
        raise ValueError("非法存储位置路径")
    return parts[1]


def resolve_path(settings: Settings, value: str) -> Path:
    parts = relative_parts(value)
    location_id = location_of(value)
    if location_id == "s3":
        from .object_types import original_path

        if parts[1] not in settings.s3_namespaces:
            raise LocationUnavailable("s3")
        root = settings.data_dir / "objects"
        path = root.joinpath(*parts[1:])
        if (
            root.is_symlink()
            or path.parent.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("媒体路径不能越过存储目录")
        ref = settings.s3_objects.get(value)
        return original_path(path, ref) if ref else path
    root = settings.data_dir if location_id == "local" else library_root(settings, location_id)
    path = root.joinpath(*(parts if location_id == "local" else parts[2:]))
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("媒体路径不能越过存储目录")
    return path


def encode_path(settings: Settings, path: Path) -> str:
    object_root = settings.data_dir / "objects"
    if path.is_relative_to(object_root):
        relative = "s3/" + path.relative_to(object_root).as_posix()
        location_of(relative)
        resolve_path(settings, relative)
        return relative
    if path.is_relative_to(settings.data_dir):
        return path.relative_to(settings.data_dir).as_posix()
    for location_id, entry in settings.storage_locations.items():
        if path.is_relative_to(entry.path / "library"):
            root = library_root(settings, location_id)
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("媒体路径不能越过存储目录")
            return f"volumes/{location_id}/{path.relative_to(root).as_posix()}"
    raise ValueError("媒体文件不在已登记的存储目录内")
