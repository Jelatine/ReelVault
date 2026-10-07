"""Offline, version-pinned title transliteration used by SQLite index triggers."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

from pypinyin import Style, lazy_pinyin

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]")


def latin(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = value.translate(str.maketrans("üǖǘǚǜ", "vvvvv")).replace("u:", "v")
    value = "".join(
        char for char in unicodedata.normalize("NFD", value) if not unicodedata.combining(char)
    )
    return value


def normalize_pinyin_query(value: str) -> str | None:
    value = latin(value)
    if re.fullmatch(r"[a-z0-9]+", value) and re.search(r"[a-z]", value):
        return value
    return None


@lru_cache(maxsize=1024)
def title_pinyin(title: str | None) -> tuple[str, str]:
    if not title or not HAN.search(title):
        return "", ""
    full = lazy_pinyin(title, style=Style.NORMAL)
    first = lazy_pinyin(title, style=Style.FIRST_LETTER)
    words = [word for part in full for word in re.sub(r"[^a-z0-9]+", " ", latin(part)).split()]
    initials = "".join(re.sub(r"[^a-z0-9]+", "", latin(part)) for part in first)
    # Compact spellings support lvxing; separated syllables support lv xing.
    return " ".join(dict.fromkeys(("".join(words), " ".join(words)))), initials


def register_pinyin(connection: Any) -> None:
    connection.create_function(
        "reelvault_casefold", 1, lambda value: (value or "").casefold(), deterministic=True
    )
    connection.create_function(
        "reelvault_pinyin", 1, lambda title: title_pinyin(title)[0], deterministic=True
    )
    connection.create_function(
        "reelvault_initials", 1, lambda title: title_pinyin(title)[1], deterministic=True
    )
