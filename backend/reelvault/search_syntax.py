"""Parse typed search conditions without interpreting SQL or FTS expressions."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import APIError
from .models import Tag, Video

PREFIX = re.compile(r"(tag|rating|duration):", re.IGNORECASE)
NUMBER = re.compile(r"(>=|<=|>|<|=)?([0-9]+(?:\.[0-9]+)?)([smh]?)", re.IGNORECASE)


@dataclass
class Search:
    terms: list[str] = field(default_factory=list)
    conditions: list[Any] = field(default_factory=list)
    tag_spans: list[tuple[int, int, str]] = field(default_factory=list)


def invalid(token: str) -> APIError:
    return APIError(
        400,
        f"搜索条件无效：{token[:160]}。请检查标签、评分或时长语法。",
        code="search_syntax_invalid",
        params={"token": token[:160]},
    )


def parse_search(query: str) -> Search:
    result = Search()
    position = 0
    while position < len(query):
        if query[position].isspace():
            position += 1
            continue
        start = position
        prefix = PREFIX.match(query, position)
        if prefix is None:
            while position < len(query) and not query[position].isspace():
                position += 1
            result.terms.append(query[start:position])
            continue
        kind = prefix[1].lower()
        position = prefix.end()
        value = ""
        if position < len(query) and query[position] == '"':
            position += 1
            closed = False
            while position < len(query):
                char = query[position]
                position += 1
                if char == '"':
                    closed = True
                    break
                if char == "\\" and position < len(query) and query[position] in ('"', "\\"):
                    char = query[position]
                    position += 1
                value += char
            if not closed or (position < len(query) and not query[position].isspace()):
                raise invalid(query[start:])
        else:
            begin = position
            while position < len(query) and not query[position].isspace():
                position += 1
            value = query[begin:position]
        token = query[start:position]
        if kind == "tag":
            if not value or not value.strip() or len(value) > 64:
                raise invalid(token)
            result.conditions.append(Video.tags.any(Tag.name == value))
            result.tag_spans.append((start, position, value))
            continue
        number = NUMBER.fullmatch(value)
        if number is None:
            raise invalid(token)
        operator, digits, unit = number.groups()
        if kind == "rating" and (unit or "." in digits):
            raise invalid(token)
        numeric = float(digits) * {"": 1, "s": 1, "m": 60, "h": 3600}[unit.lower()]
        if not math.isfinite(numeric) or numeric > 2**63 - 1:
            raise invalid(token)
        if kind == "rating" and numeric > 5:
            raise invalid(token)
        col = Video.rating if kind == "rating" else Video.duration
        result.conditions.append(
            {
                "=": col == numeric,
                ">": col > numeric,
                ">=": col >= numeric,
                "<": col < numeric,
                "<=": col <= numeric,
            }[operator or "="]
        )
    return result


def rename_query_tag(query: str, old: str, new: str) -> str:
    """Keep saved typed tag rules aligned with renames, leaving keywords intact."""
    try:
        spans = parse_search(query).tag_spans
    except APIError:
        # Historical free-text searches can contain malformed typed syntax.
        return query
    for start, end, name in reversed(spans):
        if name == old:
            query = query[:start] + "tag:" + json.dumps(new, ensure_ascii=False) + query[end:]
    return query
