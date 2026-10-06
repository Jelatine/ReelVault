from __future__ import annotations

import math


def parse_cube(data: bytes) -> tuple[bytes, dict[str, object]]:
    """Validate a single 1D/3D CUBE table and emit a canonical UTF-8 file."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("LUT 必须使用 UTF-8 编码") from error
    dimension = size = 0
    rows: list[tuple[float, float, float]] = []
    domains: dict[str, tuple[float, float, float]] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        key = fields[0]
        if key == "TITLE":
            if rows or len(fields) < 2:
                raise ValueError("LUT 标题格式错误")
            continue
        if key in ("LUT_1D_SIZE", "LUT_3D_SIZE"):
            if dimension or rows or len(fields) != 2:
                raise ValueError("LUT 只能包含一个 1D 或 3D 表")
            try:
                size = int(fields[1])
            except ValueError as error:
                raise ValueError("LUT 尺寸必须为整数") from error
            dimension = 1 if key == "LUT_1D_SIZE" else 3
            if not 2 <= size <= (65536 if dimension == 1 else 64):
                raise ValueError("1D LUT 尺寸为 2–65536，3D LUT 尺寸为 2–64")
            continue
        if key in ("DOMAIN_MIN", "DOMAIN_MAX"):
            if rows or key in domains:
                raise ValueError("LUT 输入范围重复或位置错误")
            values = fields[1:]
        else:
            if not dimension:
                raise ValueError("LUT 缺少尺寸声明")
            values = fields
        if len(values) != 3:
            raise ValueError("LUT 每行必须包含三个数值")
        try:
            triplet = tuple(float(v) for v in values)
        except ValueError as error:
            raise ValueError("LUT 包含不支持的指令或数值") from error
        if not all(math.isfinite(v) and abs(v) <= 1e6 for v in triplet):
            raise ValueError("LUT 数值必须有限且在合理范围内")
        value = (triplet[0], triplet[1], triplet[2])
        if key in ("DOMAIN_MIN", "DOMAIN_MAX"):
            domains[key] = value
        else:
            rows.append(value)
            if len(rows) > size**dimension:
                raise ValueError("LUT 数据行数超出声明尺寸")
    if not dimension or len(rows) != size**dimension:
        raise ValueError("LUT 数据行数与声明尺寸不符")
    lower = domains.get("DOMAIN_MIN", (0.0, 0.0, 0.0))
    upper = domains.get("DOMAIN_MAX", (1.0, 1.0, 1.0))
    if any(lo >= hi for lo, hi in zip(lower, upper, strict=True)):
        raise ValueError("LUT 输入范围上限必须大于下限")
    # Fixed headers and numeric rows cannot inject FFmpeg filter syntax.
    normalized = [f"LUT_{dimension}D_SIZE {size}"]
    for key, value in (("DOMAIN_MIN", lower), ("DOMAIN_MAX", upper)):
        normalized.append(key + " " + " ".join(format(v, ".17g") for v in value))
    normalized.extend(" ".join(format(v, ".17g") for v in row) for row in rows)
    return ("\n".join(normalized) + "\n").encode(), {
        "dimension": dimension,
        "size": size,
        "domain_min": list(lower),
        "domain_max": list(upper),
    }
