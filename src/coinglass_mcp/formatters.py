"""Response formatters for CoinGlass MCP tools.

Each formatter converts large raw API payloads into compact text suitable for LLM
consumption while preserving key signal fields.
"""

from __future__ import annotations

import json
import math
from contextvars import ContextVar
from datetime import datetime, timezone
from numbers import Number
from typing import Any, Callable

MAX_OUTPUT_CHARS = 4000
FALLBACK_JSON_CHARS = 3000
TOP_N = 15
TIME_SERIES_N = 24

PASS_THROUGH_TOOLS = {
    "coinglass_config",
    "coinglass_market_info",
}

_FORMAT_META_CTX: ContextVar[dict[str, Any] | None] = ContextVar(
    "coinglass_formatter_meta", default=None
)


def _int_or_none(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _extract_total_known(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("total", "total_rows", "total_count", "count", "size"):
            candidate = data.get(key)
            number = _int_or_none(candidate)
            if number is not None and number >= 0:
                return number
    return None


def _initial_format_metadata(
    data: Any,
    *,
    requested_limit: int | None = None,
    filters_applied: list[str] | None = None,
    total_known: int | None = None,
) -> dict[str, Any]:
    inferred_total = total_known if total_known is not None else _extract_total_known(data)
    return {
        "truncated": False,
        "requested_limit": requested_limit,
        "shown_rows": None,
        "total_rows": None,
        "total_known": inferred_total,
        "filters_applied": list(filters_applied or []),
        "truncation_reason": None,
    }


def _set_truncation_reason(meta: dict[str, Any], reason: str) -> None:
    current = meta.get("truncation_reason")
    if not current:
        meta["truncation_reason"] = reason
        return
    reasons = {part.strip() for part in str(current).split(",") if part.strip()}
    reasons.add(reason)
    meta["truncation_reason"] = ",".join(sorted(reasons))


def format_tool_response_with_meta(
    tool: str,
    action: str,
    data: Any,
    *,
    requested_limit: int | None = None,
    filters_applied: list[str] | None = None,
    total_known: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Dispatch formatter by tool name and capture preview/truncation metadata."""
    fn_name = f"format_{tool}"
    fn = globals().get(fn_name)
    if not callable(fn):
        raise KeyError(f"No formatter for tool '{tool}'")

    context_meta = _initial_format_metadata(
        data,
        requested_limit=requested_limit,
        filters_applied=filters_applied,
        total_known=total_known,
    )
    token = _FORMAT_META_CTX.set(context_meta)
    try:
        text = fn(action, data)
        if context_meta.get("total_rows") is not None and context_meta.get("total_known") is None:
            context_meta["total_known"] = context_meta["total_rows"]
        return text, dict(context_meta)
    finally:
        _FORMAT_META_CTX.reset(token)


def format_tool_response(tool: str, action: str, data: Any) -> str:
    """Dispatch formatter by tool name."""
    text, _ = format_tool_response_with_meta(tool, action, data)
    return text


def format_json_fallback(tool: str, action: str, data: Any, reason: str | None = None) -> str:
    """Fallback response when formatter fails or is missing."""
    header = _header(tool, action, data)
    warning = "WARNING: formatter unavailable; showing truncated JSON fallback"
    if reason:
        warning = f"WARNING: {reason}; showing truncated JSON fallback"

    raw = json.dumps(data, ensure_ascii=False, default=str)
    if len(raw) > FALLBACK_JSON_CHARS:
        body = raw[:FALLBACK_JSON_CHARS] + "... [truncated]"
    else:
        body = raw
    return _truncate([header, warning, body], total_items=None, shown_items=None)


def _pick(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None

    lowered = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        if key in obj:
            return obj[key]
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return None


def _list_payload(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in (
            "data",
            "list",
            "items",
            "rows",
            "result",
            "records",
            "history",
            "candles",
            "values",
            "series",
        ):
            val = data.get(key)
            if isinstance(val, list):
                return val
    return []


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Number):
        return float(value)
    if isinstance(value, str):
        txt = value.strip().replace(",", "")
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None
    return None


def _fmt_num(value: Any, use_suffix: bool = True) -> str:
    number = _as_float(value)
    if number is None:
        return "-"

    if not use_suffix:
        return f"{number:.2f}"

    absolute = abs(number)
    if absolute >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"{number / 1_000:.2f}K"
    return f"{number:.2f}"


def _fmt_pct(value: Any, ratio_input: bool = False) -> str:
    number = _as_float(value)
    if number is None:
        return "-"

    pct = number * 100 if ratio_input else number
    return f"{pct:.2f}%"


def _fmt_pct_auto(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    return _fmt_pct(number, ratio_input=abs(number) <= 1.0)


def _to_utc(value: Any) -> str:
    if value is None:
        return "-"

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "-"
        numeric = _as_float(stripped)
        if numeric is not None and stripped.replace(".", "", 1).replace("-", "", 1).isdigit():
            value = numeric
        else:
            iso = stripped.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return stripped

    number = _as_float(value)
    if number is None:
        return str(value)

    # Handle seconds / milliseconds / microseconds heuristically.
    if abs(number) > 1e14:
        number = number / 1_000_000
    elif abs(number) > 1e11:
        number = number / 1_000

    try:
        dt = datetime.fromtimestamp(number, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return str(value)

    return dt.strftime("%Y-%m-%d %H:%M")


def _to_utc_seconds(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return _to_utc(value)
    return _to_utc(int(number))


def _to_epoch_seconds(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        numeric = _as_float(stripped)
        if numeric is not None and stripped.replace(".", "", 1).replace("-", "", 1).isdigit():
            value = numeric
        else:
            iso = stripped.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).timestamp()

    number = _as_float(value)
    if number is None:
        return None

    if abs(number) > 1e14:
        return number / 1_000_000
    if abs(number) > 1e11:
        return number / 1_000
    return number


def _records(data: Any) -> list[dict[str, Any]]:
    payload = _list_payload(data)
    if payload:
        return [row for row in payload if isinstance(row, dict)]

    if isinstance(data, dict):
        # Some endpoints return dict-of-lists or dict-of-dicts by exchange/symbol.
        rows: list[dict[str, Any]] = []
        for parent_key, val in data.items():
            if isinstance(val, dict):
                row = dict(val)
                row.setdefault("key", parent_key)
                row.setdefault("exchange", parent_key)
                rows.append(row)
            elif isinstance(val, list) and val and isinstance(val[0], dict):
                for item in val:
                    row = dict(item)
                    row.setdefault("key", parent_key)
                    row.setdefault("exchange", parent_key)
                    rows.append(row)
        if rows:
            return rows

    return []


def _detect_symbol(data: Any) -> str:
    if isinstance(data, dict):
        value = _pick(
            data,
            "symbol",
            "pair",
            "ticker",
            "coin",
            "instrument_id",
            "fund",
            "asset",
        )
        if value is not None:
            return str(value)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _detect_symbol(data[0])
    return "-"


def _find_time_value(item: dict[str, Any]) -> Any:
    return _pick(
        item,
        "date_string",
        "create_time",
        "current_time",
        "start_time",
        "order_end_time",
        "next_funding_time",
        "t",
        "ts",
        "time",
        "timestamp",
        "date",
        "datetime",
        "nextFundingTime",
        "update_time",
        "updated_at",
    )


def _detect_timestamp(data: Any) -> str:
    max_timestamp: float | None = None

    def consider(value: Any) -> None:
        nonlocal max_timestamp
        ts = _to_epoch_seconds(value)
        if ts is None:
            return
        if max_timestamp is None or ts > max_timestamp:
            max_timestamp = ts

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                consider(_find_time_value(item))
    if isinstance(data, dict):
        consider(_find_time_value(data))
        for list_key in ("time_list", "date_list", "dateList"):
            val = data.get(list_key)
            if isinstance(val, list) and val:
                for candidate in val:
                    consider(candidate)
        rows = _records(data)
        for row in rows:
            consider(_find_time_value(row))

    if max_timestamp is None:
        return "-"
    return _to_utc(max_timestamp)


def _header(tool: str, action: str, data: Any) -> str:
    return f"{tool}({action}) | {_detect_symbol(data)} | {_detect_timestamp(data)}"


def _top(
    rows: list[dict[str, Any]],
    sort_keys: tuple[str, ...],
    limit: int = TOP_N,
    reverse: bool = True,
) -> list[dict[str, Any]]:
    def score(row: dict[str, Any]) -> float:
        for key in sort_keys:
            value = _as_float(_pick(row, key))
            if value is not None:
                return value
        return float("-inf") if reverse else float("inf")

    sorted_rows = sorted(rows, key=score, reverse=reverse)
    return sorted_rows[:limit]


def _line_from_values(values: list[str]) -> str:
    return " | ".join(values)


def _render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [_line_from_values(headers)]
    lines.extend(_line_from_values(row) for row in rows)
    return lines


def _truncate(lines: list[str], total_items: int | None, shown_items: int | None) -> str:
    context_meta = _FORMAT_META_CTX.get()
    rendered_lines = list(lines)

    if context_meta is not None:
        if shown_items is not None:
            context_meta["shown_rows"] = shown_items
        if total_items is not None:
            context_meta["total_rows"] = total_items
            if context_meta.get("total_known") is None:
                context_meta["total_known"] = total_items

    if (
        total_items is not None
        and shown_items is not None
        and shown_items < total_items
    ):
        if context_meta is not None:
            context_meta["truncated"] = True
            _set_truncation_reason(context_meta, "row_preview_limit")
        preview_line = f"preview: showing {shown_items} of {total_items} rows"
        if len(rendered_lines) > 1:
            rendered_lines.insert(1, preview_line)
        else:
            rendered_lines.append(preview_line)

    text = "\n".join(rendered_lines)
    if len(text) <= MAX_OUTPUT_CHARS:
        return text

    if context_meta is not None:
        context_meta["truncated"] = True
        _set_truncation_reason(context_meta, "max_output_chars")

    kept = list(rendered_lines)
    removed = 0
    while len("\n".join(kept)) > MAX_OUTPUT_CHARS - 40 and len(kept) > 1:
        kept.pop()
        removed += 1

    if total_items is not None and shown_items is not None:
        more_items = max(total_items - shown_items, 0) + removed
    else:
        more_items = max(removed, 1)

    suffix = (
        f"... (text capped at {MAX_OUTPUT_CHARS} chars; "
        f"{more_items} additional lines hidden)"
    )
    result = "\n".join(kept + [suffix])
    if len(result) > MAX_OUTPUT_CHARS:
        allowed = MAX_OUTPUT_CHARS - len(suffix) - 1
        result = result[:allowed].rstrip() + "\n" + suffix

    if context_meta is not None:
        if shown_items is not None and total_items is not None:
            context_meta["shown_rows"] = max(shown_items - removed, 0)
        if context_meta.get("shown_rows") is None:
            context_meta["shown_rows"] = None

    return result


def _format_passthrough(tool: str, action: str, data: Any) -> str:
    body = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    lines = [_header(tool, action, data)] + body.splitlines()
    return _truncate(lines, total_items=None, shown_items=None)


def _as_timeseries_rows(data: Any) -> list[dict[str, Any]]:
    rows = _records(data)
    if not rows and isinstance(data, dict):
        # Some endpoints are dict of arrays with aligned indexes.
        time_array = (
            data.get("time")
            or data.get("t")
            or data.get("timestamp")
            or data.get("time_list")
            or data.get("date_list")
            or data.get("dateList")
        )
        if isinstance(time_array, list):
            keys = [k for k, v in data.items() if isinstance(v, list) and len(v) == len(time_array)]
            rebuilt = []
            for idx, t in enumerate(time_array):
                item: dict[str, Any] = {"time": t}
                for key in keys:
                    item[key] = data[key][idx]
                rebuilt.append(item)
            rows = rebuilt
    return rows


def _rows_from_array_points(data: Any, columns: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _list_payload(data):
        if isinstance(item, dict):
            rows.append(item)
            continue
        if isinstance(item, (list, tuple)):
            row: dict[str, Any] = {}
            for idx, col in enumerate(columns):
                row[col] = item[idx] if idx < len(item) else None
            rows.append(row)
    return rows


def _rows_from_parallel_lists(
    data: Any,
    time_keys: tuple[str, ...],
    value_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    time_array = None
    for key in time_keys:
        candidate = data.get(key)
        if isinstance(candidate, list):
            time_array = candidate
            break
    if time_array is None:
        return []

    series_arrays: dict[str, list[Any]] = {}
    for key in value_keys:
        candidate = data.get(key)
        if isinstance(candidate, list):
            series_arrays[key] = candidate
    if not series_arrays:
        return []

    max_len = len(time_array)
    for arr in series_arrays.values():
        max_len = min(max_len, len(arr))

    rows: list[dict[str, Any]] = []
    for idx in range(max_len):
        row: dict[str, Any] = {"time": time_array[idx]}
        for key, arr in series_arrays.items():
            row[key] = arr[idx]
        rows.append(row)
    return rows


def _classify_fear_greed(value: Any) -> str:
    score = _as_float(value)
    if score is None:
        return "-"
    if score <= 24:
        return "Extreme Fear"
    if score <= 49:
        return "Fear"
    if score <= 54:
        return "Neutral"
    if score <= 74:
        return "Greed"
    return "Extreme Greed"


def _format_last_points(
    tool: str,
    action: str,
    data: Any,
    headers: list[str],
    builder: Callable[[dict[str, Any]], list[str]],
    limit: int = TIME_SERIES_N,
) -> str:
    rows = _as_timeseries_rows(data)
    if not rows:
        return _truncate([_header(tool, action, data), "No data returned"], None, None)

    selected = rows[-limit:]
    table_rows = [builder(item) for item in selected]
    lines = [_header(tool, action, data)] + _render_table(headers, table_rows)
    return _truncate(lines, total_items=len(rows), shown_items=len(selected))


def _format_generic_top(
    tool: str,
    action: str,
    data: Any,
    headers: list[str],
    builder: Callable[[dict[str, Any]], list[str]],
    sort_keys: tuple[str, ...],
    limit: int = TOP_N,
) -> str:
    rows = _records(data)
    if not rows:
        return _truncate([_header(tool, action, data), "No data returned"], None, None)

    selected = _top(rows, sort_keys=sort_keys, limit=limit, reverse=True)
    table_rows = [builder(item) for item in selected]
    lines = [_header(tool, action, data)] + _render_table(headers, table_rows)
    return _truncate(lines, total_items=len(rows), shown_items=len(selected))


def _safe_ratio(num: Any, den: Any) -> str:
    num_f = _as_float(num)
    den_f = _as_float(den)
    if num_f is None or den_f in (None, 0.0):
        return "-"
    return f"{(num_f / den_f):.2f}"


def _extract_depth_levels(data: Any) -> list[tuple[float, float]]:
    """Extract (price, depth_value) pairs from heatmap-like payloads."""
    levels: list[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            price = _as_float(_pick(node, "price", "y", "level", "price_level"))
            depth = _as_float(
                _pick(
                    node,
                    "depth",
                    "value",
                    "volume",
                    "liq",
                    "liquidation",
                    "total",
                    "amount",
                )
            )
            if price is not None and depth is not None:
                levels.append((price, abs(depth)))

            y_axis = node.get("y_axis") or node.get("yAxis")
            matrix = (
                node.get("liquidation_leverage_data")
                or node.get("z")
                or node.get("values")
                or node.get("data")
            )
            if isinstance(y_axis, list) and isinstance(matrix, list):
                # Sparse matrix format: [[y_index, x_timestamp, value], ...]
                if matrix and isinstance(matrix[0], (list, tuple)) and len(matrix[0]) >= 3:
                    for point in matrix:
                        if not isinstance(point, (list, tuple)) or len(point) < 3:
                            continue
                        y_idx_f = _as_float(point[0])
                        value_f = _as_float(point[2])
                        if y_idx_f is None or value_f is None:
                            continue
                        y_idx = int(y_idx_f)
                        if y_idx < 0 or y_idx >= len(y_axis):
                            continue
                        price_level = _as_float(y_axis[y_idx])
                        if price_level is None:
                            continue
                        levels.append((price_level, abs(value_f)))
                else:
                    for idx, y in enumerate(y_axis):
                        price_level = _as_float(y)
                        if price_level is None:
                            continue
                        row_value = matrix[idx] if idx < len(matrix) else None
                        if isinstance(row_value, list):
                            total_depth = sum(abs(_as_float(v) or 0.0) for v in row_value)
                        else:
                            total_depth = abs(_as_float(row_value) or 0.0)
                        if total_depth:
                            levels.append((price_level, total_depth))

            for val in node.values():
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    dedup: dict[float, float] = {}
    for price, depth in levels:
        dedup[price] = dedup.get(price, 0.0) + depth
    return sorted(dedup.items(), key=lambda x: x[1], reverse=True)


def _extract_liq_map_rows(data: Any) -> list[dict[str, Any]]:
    payload: Any = data
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        payload = data["data"]
    if not isinstance(payload, dict):
        return []

    rows: list[dict[str, Any]] = []
    for price_key, entries in payload.items():
        price = _as_float(price_key)
        if price is None:
            continue
        if not isinstance(entries, list):
            continue
        long_total = 0.0
        short_total = 0.0
        total = 0.0
        for entry in entries:
            leverage = None
            volume = None
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                leverage = _as_float(entry[0])
                volume = _as_float(entry[1])
            elif isinstance(entry, dict):
                leverage = _as_float(_pick(entry, "leverage", "level", "x"))
                volume = _as_float(_pick(entry, "volume", "value", "liq", "liquidation"))
            vol = abs(volume or 0.0)
            if vol == 0:
                continue
            total += vol
            if leverage is None:
                continue
            if leverage < 0:
                short_total += vol
            else:
                long_total += vol
        if total == 0:
            continue
        if long_total == 0.0 and short_total == 0.0:
            long_total = total / 2
            short_total = total / 2
        rows.append({
            "price": price,
            "long": long_total,
            "short": short_total,
            "total": total,
        })
    return rows


def _bucket_label(price: float) -> str:
    absolute = abs(price)
    if absolute >= 100_000:
        step = 1_000
    elif absolute >= 10_000:
        step = 100
    elif absolute >= 1_000:
        step = 10
    elif absolute >= 100:
        step = 1
    else:
        step = 0.1
    low = math.floor(price / step) * step
    high = low + step
    return f"{_fmt_num(low, use_suffix=False)}-{_fmt_num(high, use_suffix=False)}"


def _format_default(tool: str, action: str, data: Any, limit: int = TIME_SERIES_N) -> str:
    rows = _records(data)
    if not rows:
        if isinstance(data, dict):
            pairs = []
            for key, value in list(data.items())[:TOP_N]:
                if isinstance(value, (dict, list)):
                    pairs.append(f"{key}=<{type(value).__name__}>")
                else:
                    pairs.append(f"{key}={_fmt_num(value) if _as_float(value) is not None else value}")
            body = "; ".join(pairs) if pairs else "No data returned"
            return _truncate([_header(tool, action, data), body], None, None)
        return _truncate([_header(tool, action, data), "No data returned"], None, None)

    selected = rows[-limit:]
    cols = sorted({k for row in selected for k in row.keys()})[:6]
    table_rows: list[list[str]] = []
    for row in selected:
        out: list[str] = []
        for col in cols:
            value = row.get(col)
            if col.lower() in {"time", "timestamp", "date", "datetime", "t", "ts"}:
                out.append(_to_utc(value))
            elif _as_float(value) is not None:
                out.append(_fmt_num(value))
            else:
                out.append(str(value) if value is not None else "-")
        table_rows.append(out)

    lines = [_header(tool, action, data)] + _render_table(cols, table_rows)
    return _truncate(lines, total_items=len(rows), shown_items=len(selected))


# =============================================================================
# TOOL FORMATTERS (26)
# =============================================================================


def format_coinglass_calendar(action: str, data: Any) -> str:
    tool = "coinglass_calendar"
    rows = _records(data)
    if not rows:
        return _format_passthrough(tool, action, data)

    # Filter by importance_level >= 2 (medium + high impact)
    important = [r for r in rows if (_as_float(_pick(r, "importance_level", "importance", "star")) or 0) >= 2]
    if not important:
        important = rows  # fallback: show all if no importance field

    # Sort by time descending (newest first), limit
    important.sort(key=lambda r: _as_float(_pick(r, "publish_timestamp", "time", "timestamp") or _find_time_value(r)) or 0, reverse=True)
    selected = important[:TOP_N]

    stars_map = {1: "★", 2: "★★", 3: "★★★"}
    lines = [_header(tool, action, data)]
    lines += _render_table(
        ["time", "impact", "country", "event", "actual", "forecast", "previous"],
        [
            [
                _to_utc(_pick(r, "publish_timestamp", "time", "timestamp") or _find_time_value(r)),
                stars_map.get(int(_as_float(_pick(r, "importance_level", "importance")) or 0), "?"),
                str(_pick(r, "country_code", "country_name", "country") or "-"),
                str(_pick(r, "calendar_name", "event_name", "title", "name") or "-")[:60],
                str(_pick(r, "published_value", "actual_value", "actual") or "-"),
                str(_pick(r, "forecast_value", "forecast") or "-"),
                str(_pick(r, "previous_value", "previous") or "-"),
            ]
            for r in selected
        ],
    )
    return _truncate(lines, total_items=len(important), shown_items=len(selected))


def format_coinglass_market_info(action: str, data: Any) -> str:
    return _format_passthrough("coinglass_market_info", action, data)


def format_coinglass_market_data(action: str, data: Any) -> str:
    tool = "coinglass_market_data"

    if action == "coins_summary":
        row = data if isinstance(data, dict) else (_records(data)[0] if _records(data) else None)
        if not isinstance(row, dict):
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        lines = [
            _header(tool, action, data),
            f"price={_fmt_num(_pick(row, 'current_price', 'price', 'close', 'c'), use_suffix=False)}",
            (
                "price_changes="
                f"5m:{_fmt_pct(_pick(row, 'price_change_percent_5m'))}, "
                f"1h:{_fmt_pct(_pick(row, 'price_change_percent_1h', 'change_1h'))}, "
                f"4h:{_fmt_pct(_pick(row, 'price_change_percent_4h', 'change_4h'))}, "
                f"24h:{_fmt_pct(_pick(row, 'price_change_percent_24h', 'change_24h'))}"
            ),
            (
                "oi="
                f"{_fmt_num(_pick(row, 'open_interest_usd', 'oi'))}, "
                f"chg_1h:{_fmt_pct(_pick(row, 'open_interest_change_percent_1h'))}, "
                f"chg_4h:{_fmt_pct(_pick(row, 'open_interest_change_percent_4h'))}, "
                f"chg_24h:{_fmt_pct(_pick(row, 'open_interest_change_percent_24h'))}"
            ),
            (
                "volume="
                f"chg_1h:{_fmt_pct(_pick(row, 'volume_change_percent_1h'))}, "
                f"chg_4h:{_fmt_pct(_pick(row, 'volume_change_percent_4h'))}, "
                f"chg_24h:{_fmt_pct(_pick(row, 'volume_change_percent_24h'))}"
            ),
            (
                "liq_24h="
                f"long:{_fmt_num(_pick(row, 'long_liquidation_usd_24h'))}, "
                f"short:{_fmt_num(_pick(row, 'short_liquidation_usd_24h'))}, "
                f"total:{_fmt_num(_pick(row, 'liquidation_usd_24h'))}"
            ),
            (
                "ls_ratio="
                f"1h:{_fmt_num(_pick(row, 'long_short_ratio_1h'), use_suffix=False)}, "
                f"4h:{_fmt_num(_pick(row, 'long_short_ratio_4h'), use_suffix=False)}, "
                f"24h:{_fmt_num(_pick(row, 'long_short_ratio_24h'), use_suffix=False)}"
            ),
            (
                "meta="
                f"mcap:{_fmt_num(_pick(row, 'market_cap_usd', 'mcap'))}, "
                f"funding:{_fmt_pct(_pick(row, 'avg_funding_rate_by_oi', 'funding_rate'), ratio_input=True)}, "
                f"oi_mcap:{_fmt_pct(_pick(row, 'open_interest_market_cap_ratio'), ratio_input=True)}"
            ),
        ]
        return _truncate(lines, None, None)

    if action == "pairs_summary":
        return _format_generic_top(
            tool,
            action,
            data,
            ["pair", "exchange", "price", "chg_24h", "oi", "volume"],
            lambda r: [
                str(_pick(r, "instrument_id", "symbol", "pair") or "-"),
                str(_pick(r, "exchange_name", "exchange", "exName") or "-"),
                _fmt_num(_pick(r, "current_price", "price", "close", "c"), use_suffix=False),
                _fmt_pct(_pick(r, "price_change_percent_24h", "change_24h", "change24h")),
                _fmt_num(_pick(r, "open_interest_usd", "oi", "open_interest", "openInterest")),
                _fmt_num(_pick(r, "volume_usd", "volume_24h", "volume24h", "volume")),
            ],
            sort_keys=("volume_usd", "volume_24h", "volume24h", "volume"),
        )

    if action == "price_changes":
        return _format_generic_top(
            tool,
            action,
            data,
            ["symbol", "1h", "4h", "24h"],
            lambda r: [
                str(_pick(r, "symbol", "pair", "coin") or "-"),
                _fmt_pct(_pick(r, "price_change_percent_1h", "change_1h", "h1")),
                _fmt_pct(_pick(r, "price_change_percent_4h", "change_4h", "h4")),
                _fmt_pct(_pick(r, "price_change_percent_24h", "change_24h", "d1")),
            ],
            sort_keys=("price_change_percent_24h", "change_24h", "d1"),
        )

    if action == "volume_footprint":
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "buy_vol", "sell_vol", "ratio"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(
                    _pick(
                        r,
                        "buy_vol_usd",
                        "buy_volume_usd",
                        "taker_buy_volume_usd",
                        "buy_volume",
                        "buyVol",
                        "buy",
                    )
                ),
                _fmt_num(
                    _pick(
                        r,
                        "sell_vol_usd",
                        "sell_volume_usd",
                        "taker_sell_volume_usd",
                        "sell_volume",
                        "sellVol",
                        "sell",
                    )
                ),
                _safe_ratio(
                    _pick(
                        r,
                        "buy_vol_usd",
                        "buy_volume_usd",
                        "taker_buy_volume_usd",
                        "buy_volume",
                        "buyVol",
                        "buy",
                    ),
                    _pick(
                        r,
                        "sell_vol_usd",
                        "sell_volume_usd",
                        "taker_sell_volume_usd",
                        "sell_volume",
                        "sellVol",
                        "sell",
                    ),
                ),
            ],
        )

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_price_history(action: str, data: Any) -> str:
    tool = "coinglass_price_history"
    if action != "price_history":
        raise ValueError(f"No formatter action for {tool}:{action}")

    return _format_last_points(
        tool,
        action,
        data,
        ["time", "open", "high", "low", "close", "volume"],
        lambda r: [
            _to_utc(_find_time_value(r)),
            _fmt_num(_pick(r, "o", "open"), use_suffix=False),
            _fmt_num(_pick(r, "h", "high"), use_suffix=False),
            _fmt_num(_pick(r, "l", "low"), use_suffix=False),
            _fmt_num(_pick(r, "c", "close"), use_suffix=False),
            _fmt_num(_pick(r, "v", "volume", "vol", "volume_usd", "volumeUsd")),
        ],
    )


def format_coinglass_oi_history(action: str, data: Any) -> str:
    tool = "coinglass_oi_history"
    if action not in {"aggregated", "pair", "stablecoin", "coin_margin"}:
        raise ValueError(f"No formatter action for {tool}:{action}")

    def _build_row(r: dict[str, Any]) -> list[str]:
        open_val = _pick(r, "open", "o")
        high_val = _pick(r, "high", "h")
        low_val = _pick(r, "low", "l")
        close_val = _pick(r, "close", "c", "open_interest", "oi", "openInterest")
        change_pct = _pick(
            r,
            "open_interest_change_percent",
            "oi_change",
            "oi_change_percent",
            "change_percent",
            "change",
        )
        if change_pct is None:
            close_f = _as_float(close_val)
            open_f = _as_float(open_val)
            if close_f is not None and open_f not in (None, 0.0):
                change_pct = (close_f - open_f) / open_f * 100
        # Show OHLC if available, otherwise just close
        if high_val is not None and low_val is not None:
            return [
                _to_utc(_find_time_value(r)),
                _fmt_num(open_val),
                _fmt_num(high_val),
                _fmt_num(low_val),
                _fmt_num(close_val),
                _fmt_pct(change_pct),
            ]
        return [
            _to_utc(_find_time_value(r)),
            _fmt_num(close_val),
            _fmt_pct(change_pct),
        ]

    # Peek at first record to determine columns
    rows = _records(data)
    has_ohlc = False
    if rows:
        first = rows[0]
        has_ohlc = _pick(first, "high", "h") is not None

    cols = ["time", "open", "high", "low", "close", "change"] if has_ohlc else ["time", "oi", "oi_change"]
    return _format_last_points(tool, action, data, cols, _build_row)


def format_coinglass_oi_distribution(action: str, data: Any) -> str:
    tool = "coinglass_oi_distribution"

    if action == "by_exchange":
        rows = _records(data)
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        selected = _top(rows, sort_keys=("open_interest_usd", "oi_usd", "oi"), limit=TOP_N)
        total_oi = sum(
            _as_float(_pick(r, "open_interest_usd", "oi_usd", "oi")) or 0.0 for r in rows
        )
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["exchange", "oi_usd", "oi_chg_24h", "share"],
            [
                [
                    str(_pick(r, "exchange", "exchange_name", "exName", "key") or "-"),
                    _fmt_num(_pick(r, "open_interest_usd", "oi_usd", "oi")),
                    _fmt_pct(
                        _pick(
                            r,
                            "open_interest_change_percent_24h",
                            "change_24h",
                            "oi_change_24h",
                            "change24h",
                        )
                    ),
                    _fmt_pct(
                        (
                            ((_as_float(_pick(r, "open_interest_usd", "oi_usd", "oi")) or 0.0) / total_oi)
                            * 100
                        )
                        if total_oi > 0
                        else None
                    ),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    if action == "exchange_chart":
        by_exchange: dict[str, list[dict[str, Any]]] = {}
        if isinstance(data, dict):
            time_list = data.get("time_list")
            data_map = data.get("data_map")
            if isinstance(time_list, list) and isinstance(data_map, dict):
                for exchange, series in data_map.items():
                    if not isinstance(series, list):
                        continue
                    size = min(len(time_list), len(series))
                    built = []
                    for idx in range(size):
                        built.append({
                            "exchange": exchange,
                            "time": time_list[idx],
                            "open_interest_usd": series[idx],
                        })
                    if built:
                        by_exchange[str(exchange)] = built

        if not by_exchange:
            rows = _records(data)
            for row in rows:
                ex = str(_pick(row, "exchange", "exchange_name", "exName", "key") or "unknown")
                by_exchange.setdefault(ex, []).append(row)

        if not by_exchange:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        ranked = sorted(
            by_exchange.items(),
            key=lambda kv: _as_float(
                _pick(kv[1][-1], "open_interest_usd", "oi_usd", "oi", "close", "c")
            )
            or 0.0,
            reverse=True,
        )[:5]

        lines = [_header(tool, action, data), "exchange | time | oi"]
        total = 0
        shown = 0
        for ex, series in ranked:
            total += len(series)
            tail = series[-5:]
            shown += len(tail)
            for item in tail:
                lines.append(
                    _line_from_values(
                        [
                            ex,
                            _to_utc(_find_time_value(item)),
                            _fmt_num(_pick(item, "open_interest_usd", "oi_usd", "oi", "close", "c")),
                        ]
                    )
                )
        return _truncate(lines, total_items=total, shown_items=shown)

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_funding_current(action: str, data: Any) -> str:
    tool = "coinglass_funding_current"

    if action == "rates":
        rows = _records(data)
        flattened: list[dict[str, Any]] = []
        for row in rows:
            symbol = _pick(row, "symbol")
            nested_found = False
            margin_labels = {"stablecoin_margin_list": "USDT", "token_margin_list": "COIN"}
            for list_key, margin_type in margin_labels.items():
                series = row.get(list_key)
                if not isinstance(series, list):
                    continue
                nested_found = True
                for item in series:
                    if not isinstance(item, dict):
                        continue
                    merged = dict(item)
                    if symbol is not None:
                        merged.setdefault("symbol", symbol)
                    merged["_margin_type"] = margin_type
                    flattened.append(merged)
            if not nested_found:
                flattened.append(row)
        rows = flattened
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        # Ensure major exchanges are always visible (they often have moderate rates)
        MAJOR_EXCHANGES = {"binance", "bybit", "okx", "bitget", "deribit"}
        selected = _top(rows, sort_keys=("funding_rate", "rate", "fundingRate"), limit=TOP_N)
        selected_names = {str(_pick(r, "exchange", "exchange_name", "exName") or "").lower() for r in selected}
        # Add missing major exchanges from the full list
        for row in rows:
            ex_name = str(_pick(row, "exchange", "exchange_name", "exName") or "").lower()
            if ex_name in MAJOR_EXCHANGES and ex_name not in selected_names:
                selected.append(row)
                selected_names.add(ex_name)
        rates = [_as_float(_pick(r, "funding_rate", "rate", "fundingRate")) for r in rows]
        clean_rates = [x for x in rates if x is not None]

        lines = [_header(tool, action, data)]
        if clean_rates:
            avg_rate = sum(clean_rates) / len(clean_rates)
            lines.append(
                "summary="
                f"avg:{_fmt_pct(avg_rate, ratio_input=True)}, "
                f"min:{_fmt_pct(min(clean_rates), ratio_input=True)}, "
                f"max:{_fmt_pct(max(clean_rates), ratio_input=True)}"
            )

        lines += _render_table(
            ["exchange", "margin", "funding_rate", "next_funding"],
            [
                [
                    str(_pick(r, "exchange", "exchange_name", "exName") or "-"),
                    str(r.get("_margin_type", "-")),
                    _fmt_pct(_pick(r, "funding_rate", "rate", "fundingRate"), ratio_input=True),
                    _to_utc(_pick(r, "next_funding_time", "nextFundingTime", "time")),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    if action == "accumulated":
        rows = _records(data)
        flattened: list[dict[str, Any]] = []
        for row in rows:
            symbol = _pick(row, "symbol")
            nested_found = False
            for list_key in ("stablecoin_margin_list", "token_margin_list"):
                series = row.get(list_key)
                if not isinstance(series, list):
                    continue
                nested_found = True
                for item in series:
                    if not isinstance(item, dict):
                        continue
                    merged = dict(item)
                    if symbol is not None:
                        merged.setdefault("symbol", symbol)
                    flattened.append(merged)
            if not nested_found:
                flattened.append(row)

        if not flattened:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        selected = _top(flattened, sort_keys=("funding_rate", "accumulated_rate", "rate", "value"), limit=TOP_N)
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["exchange", "symbol", "acc_rate"],
            [
                [
                    str(_pick(r, "exchange", "exchange_name", "exName") or "-"),
                    str(_pick(r, "symbol", "pair") or "-"),
                    _fmt_pct(_pick(r, "funding_rate", "accumulated_rate", "rate", "value"), ratio_input=True),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(flattened), shown_items=len(selected))

    if action == "arbitrage":
        rows = _records(data)
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        selected = _top(rows, sort_keys=("apr", "spread", "spread_rate", "diff"), limit=TOP_N)
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["symbol", "buy_exchange", "sell_exchange", "spread", "apr"],
            [
                [
                    str(_pick(r, "symbol", "pair") or "-"),
                    str(
                        _pick(
                            _pick(r, "buy") if isinstance(_pick(r, "buy"), dict) else {},
                            "exchange",
                            "name",
                        )
                        or _pick(r, "long_exchange", "buy_exchange", "exchange_buy")
                        or "-"
                    ),
                    str(
                        _pick(
                            _pick(r, "sell") if isinstance(_pick(r, "sell"), dict) else {},
                            "exchange",
                            "name",
                        )
                        or _pick(r, "short_exchange", "sell_exchange", "exchange_sell")
                        or "-"
                    ),
                    _fmt_num(_pick(r, "spread", "spread_rate", "diff"), use_suffix=False),
                    _fmt_num(_pick(r, "apr", "annualized_apr"), use_suffix=False),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_funding_history(action: str, data: Any) -> str:
    tool = "coinglass_funding_history"
    if action not in {"pair", "oi_weighted", "vol_weighted"}:
        raise ValueError(f"No formatter action for {tool}:{action}")

    def _build_row(r: dict[str, Any]) -> list[str]:
        open_val = _pick(r, "open", "o")
        high_val = _pick(r, "high", "h")
        low_val = _pick(r, "low", "l")
        close_val = _pick(r, "close", "c", "funding_rate", "rate", "fundingRate")
        if high_val is not None and low_val is not None:
            return [
                _to_utc(_find_time_value(r)),
                _fmt_pct(open_val, ratio_input=True),
                _fmt_pct(high_val, ratio_input=True),
                _fmt_pct(low_val, ratio_input=True),
                _fmt_pct(close_val, ratio_input=True),
            ]
        return [
            _to_utc(_find_time_value(r)),
            _fmt_pct(close_val, ratio_input=True),
        ]

    rows = _records(data)
    has_ohlc = False
    if rows:
        has_ohlc = _pick(rows[0], "high", "h") is not None

    cols = ["time", "open", "high", "low", "close"] if has_ohlc else ["time", "rate"]
    return _format_last_points(tool, action, data, cols, _build_row)


def format_coinglass_long_short(action: str, data: Any) -> str:
    tool = "coinglass_long_short"

    if action in {"global", "top_accounts", "top_positions", "taker_ratio"}:
        rows = _rows_from_array_points(
            data, ("time", "long_rate", "short_rate", "long_short_ratio")
        )
        if not rows:
            rows = _as_timeseries_rows(data)
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        selected = rows[-TIME_SERIES_N:]
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["time", "long", "short", "ratio"],
            [
                [
                    _to_utc(_find_time_value(r)),
                    _fmt_pct_auto(
                        _pick(
                            r,
                            "global_account_long_percent",
                            "top_account_long_percent",
                            "top_position_long_percent",
                            "longRate",
                            "long_rate",
                            "longRatio",
                            "long_ratio",
                            "buy_ratio",
                            "long",
                        ),
                    ),
                    _fmt_pct_auto(
                        _pick(
                            r,
                            "global_account_short_percent",
                            "top_account_short_percent",
                            "top_position_short_percent",
                            "shortRate",
                            "short_rate",
                            "shortRatio",
                            "short_ratio",
                            "sell_ratio",
                            "short",
                        ),
                    ),
                    _fmt_num(
                        _pick(
                            r,
                            "global_account_long_short_ratio",
                            "top_account_long_short_ratio",
                            "top_position_long_short_ratio",
                            "longShortRatio",
                            "long_short_ratio",
                            "ratio",
                        ),
                        use_suffix=False,
                    ),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    if action in {"net_position", "net_position_v2"}:
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "net_long", "net_short"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(_pick(r, "net_long", "long", "long_volume", "longVol", "long_position")),
                _fmt_num(_pick(r, "net_short", "short", "short_volume", "shortVol", "short_position")),
            ],
        )

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_liq_history(action: str, data: Any) -> str:
    tool = "coinglass_liq_history"

    if action in {"aggregated", "pair"}:
        rows = _rows_from_parallel_lists(
            data,
            time_keys=("dateList", "time_list", "time"),
            value_keys=("long_volUsd", "short_volUsd", "longList", "shortList"),
        )
        if rows:
            for row in rows:
                row["long_volUsd"] = _pick(row, "long_volUsd", "longList")
                row["short_volUsd"] = _pick(row, "short_volUsd", "shortList")
                row["long_liquidation_usd"] = row.get("longList")
                row["short_liquidation_usd"] = row.get("shortList")
            data = rows

        return _format_last_points(
            tool,
            action,
            data,
            ["time", "long_liq", "short_liq", "total"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(
                    _pick(
                        r,
                        "long_volUsd",
                        "aggregated_long_liquidation_usd",
                        "long_liquidation_usd",
                        "longLiquidation_usd",
                        "long_liq",
                        "longLiq",
                        "long",
                        "longUsd",
                    )
                ),
                _fmt_num(
                    _pick(
                        r,
                        "short_volUsd",
                        "aggregated_short_liquidation_usd",
                        "short_liquidation_usd",
                        "shortLiquidation_usd",
                        "short_liq",
                        "shortLiq",
                        "short",
                        "shortUsd",
                    )
                ),
                _fmt_num(
                    (
                        _as_float(
                            _pick(
                                r,
                                "long_volUsd",
                                "aggregated_long_liquidation_usd",
                                "long_liquidation_usd",
                                "longLiquidation_usd",
                                "long_liq",
                                "longLiq",
                                "long",
                                "longUsd",
                            )
                        )
                        or 0.0
                    )
                    + (
                        _as_float(
                            _pick(
                                r,
                                "short_volUsd",
                                "aggregated_short_liquidation_usd",
                                "short_liquidation_usd",
                                "shortLiquidation_usd",
                                "short_liq",
                                "shortLiq",
                                "short",
                                "shortUsd",
                            )
                        )
                        or 0.0
                    )
                ),
            ],
        )

    if action == "by_coin":
        return _format_generic_top(
            tool,
            action,
            data,
            ["symbol", "long_liq", "short_liq", "total", "long_pct"],
            lambda r: [
                str(_pick(r, "symbol", "coin") or "-"),
                _fmt_num(_pick(r, "long_liquidation_usd_24h", "long_liquidation_usd", "long_liq", "longLiq", "long")),
                _fmt_num(_pick(r, "short_liquidation_usd_24h", "short_liquidation_usd", "short_liq", "shortLiq", "short")),
                _fmt_num(_pick(r, "liquidation_usd_24h", "liquidation_usd", "total", "liq_usd", "amount")),
                _fmt_pct(
                    (
                        (_as_float(_pick(r, "long_liquidation_usd_24h", "long_liquidation_usd", "long_liq", "longLiq", "long")) or 0.0)
                        / max(
                            (_as_float(_pick(r, "liquidation_usd_24h", "liquidation_usd", "total", "liq_usd", "amount")) or 0.0),
                            1e-12,
                        )
                    )
                    * 100
                ),
            ],
            sort_keys=("liquidation_usd_24h", "liquidation_usd", "total", "liq_usd", "amount"),
        )

    if action == "by_exchange":
        return _format_generic_top(
            tool,
            action,
            data,
            ["exchange", "liq_usd", "long_pct", "short_pct"],
            lambda r: [
                str(_pick(r, "exchange", "exchange_name", "exName", "key") or "-"),
                _fmt_num(_pick(r, "liquidation_usd", "liq_usd", "total", "amount", "liquidation")),
                _fmt_pct(
                    (
                        (
                            _as_float(
                                _pick(
                                    r,
                                    "longLiquidation_usd",
                                    "long_liquidation_usd",
                                    "long_liq",
                                    "longLiq",
                                )
                            )
                            or 0.0
                        )
                        / max((_as_float(_pick(r, "liquidation_usd", "liq_usd", "total")) or 0.0), 1e-12)
                    )
                    * 100
                ),
                _fmt_pct(
                    (
                        (
                            _as_float(
                                _pick(
                                    r,
                                    "shortLiquidation_usd",
                                    "short_liquidation_usd",
                                    "short_liq",
                                    "shortLiq",
                                )
                            )
                            or 0.0
                        )
                        / max((_as_float(_pick(r, "liquidation_usd", "liq_usd", "total")) or 0.0), 1e-12)
                    )
                    * 100
                ),
            ],
            sort_keys=("liquidation_usd", "liq_usd", "total", "amount", "liquidation"),
        )

    if action == "max_pain":
        row = data if isinstance(data, dict) else (_records(data)[0] if _records(data) else None)
        if not isinstance(row, dict):
            return _truncate([_header(tool, action, data), "No data returned"], None, None)
        lines = [
            _header(tool, action, data),
            (
                "long_max_pain="
                f"price:{_fmt_num(_pick(row, 'long_max_pain_liq_price', 'long_max_pain_price', 'longMaxPainPrice'), use_suffix=False)}, "
                f"level:{_fmt_num(_pick(row, 'long_max_pain_liq_level', 'long_max_pain_level', 'longMaxPainLevel'))}"
            ),
            (
                "short_max_pain="
                f"price:{_fmt_num(_pick(row, 'short_max_pain_liq_price', 'short_max_pain_price', 'shortMaxPainPrice'), use_suffix=False)}, "
                f"level:{_fmt_num(_pick(row, 'short_max_pain_liq_level', 'short_max_pain_level', 'shortMaxPainLevel'))}"
            ),
        ]
        return _truncate(lines, None, None)

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_liq_orders(action: str, data: Any) -> str:
    tool = "coinglass_liq_orders"
    if action != "orders":
        raise ValueError(f"No formatter action for {tool}:{action}")

    rows = _records(data)
    if not rows:
        return _truncate([_header(tool, action, data), "No data returned"], None, None)

    buckets: dict[str, dict[str, float]] = {}
    for row in rows:
        price = _as_float(_pick(row, "price", "mark_price", "trigger_price"))
        value = _as_float(_pick(row, "usd_value", "value", "volume", "amount", "liq_usd")) or 0.0
        side_raw = str(_pick(row, "side", "direction", "position_side") or "").lower()
        if price is None:
            continue

        label = _bucket_label(price)
        cell = buckets.setdefault(label, {"long": 0.0, "short": 0.0})
        # CoinGlass liq orders: side=1 = long liquidation (forced sell), side=2 = short liquidation (forced buy)
        # This is OPPOSITE to large_orders convention
        if "long" in side_raw or side_raw == "1":
            cell["long"] += value
        elif "short" in side_raw or side_raw == "2":
            cell["short"] += value
        else:
            # Unknown side: split evenly to keep total accounted.
            cell["long"] += value / 2
            cell["short"] += value / 2

    ranked = sorted(
        buckets.items(),
        key=lambda kv: kv[1]["long"] + kv[1]["short"],
        reverse=True,
    )[:TOP_N]

    lines = [_header(tool, action, data)]
    lines += _render_table(
        ["price_range", "long_liq_vol", "short_liq_vol"],
        [
            [
                label,
                _fmt_num(vals["long"]),
                _fmt_num(vals["short"]),
            ]
            for label, vals in ranked
        ],
    )
    return _truncate(lines, total_items=len(buckets), shown_items=len(ranked))


def format_coinglass_liq_heatmap(action: str, data: Any) -> str:
    tool = "coinglass_liq_heatmap"

    if action in {"coin_heatmap", "pair_heatmap"}:
        # Try rich 3D format first: y_axis + liquidation_leverage_data
        y_axis = data.get("y_axis", []) if isinstance(data, dict) else []
        lev_data = data.get("liquidation_leverage_data", []) if isinstance(data, dict) else []

        if y_axis and lev_data:
            # Aggregate by price level with leverage buckets
            from collections import defaultdict
            by_price_total = defaultdict(float)
            by_price_lev = defaultdict(lambda: defaultdict(float))
            for point in lev_data:
                if not isinstance(point, (list, tuple)) or len(point) < 3:
                    continue
                y_idx_raw, lev_raw, vol_raw = point[0], point[1], point[2]
                y_idx_num = _as_float(y_idx_raw)
                lev = _as_float(lev_raw)
                vol = _as_float(vol_raw)
                if y_idx_num is None or lev is None or vol is None:
                    continue
                y_idx = int(y_idx_num)
                if y_idx < 0 or y_idx >= len(y_axis):
                    continue
                price = _as_float(y_axis[y_idx])
                if price is None or price <= 0:
                    continue

                by_price_total[price] += vol
                # Group into buckets: low (≤10x), med (11-25x), high (26-50x), degen (>50x)
                if lev <= 10:
                    by_price_lev[price]["low"] += vol
                elif lev <= 25:
                    by_price_lev[price]["med"] += vol
                elif lev <= 50:
                    by_price_lev[price]["high"] += vol
                else:
                    by_price_lev[price]["degen"] += vol

            if by_price_total:
                # Balanced above/below median — more levels for heatmap (10 per side)
                all_prices = sorted(by_price_total.keys())
                median_price = all_prices[len(all_prices) // 2]
                HEATMAP_PER_SIDE = 10
                half = HEATMAP_PER_SIDE

                above = sorted(
                    [(p, v) for p, v in by_price_total.items() if p >= median_price],
                    key=lambda x: x[1], reverse=True
                )[:half]
                below = sorted(
                    [(p, v) for p, v in by_price_total.items() if p < median_price],
                    key=lambda x: x[1], reverse=True
                )[:half]
                top_levels = sorted(above + below, key=lambda x: x[0], reverse=True)

                # Add direction labels: above median = SHORT_PAIN (squeeze), below = LONG_PAIN (dump)
                above_total = sum(v for p, v in above)
                below_total = sum(v for p, v in below)
                # Nearest magnets (highest volume level per side)
                nearest_short = max(above, key=lambda x: x[1]) if above else None
                nearest_long = max(below, key=lambda x: x[1]) if below else None
                lines = [_header(tool, action, data)]
                summary_parts = [
                    f"short_pain_above:{_fmt_num(above_total)}",
                    f"long_pain_below:{_fmt_num(below_total)}",
                    f"median_price:{_fmt_num(median_price, use_suffix=False)}",
                ]
                if nearest_short:
                    summary_parts.append(f"nearest_short_magnet:{_fmt_num(nearest_short[0], use_suffix=False)}({_fmt_num(nearest_short[1])})")
                if nearest_long:
                    summary_parts.append(f"nearest_long_magnet:{_fmt_num(nearest_long[0], use_suffix=False)}({_fmt_num(nearest_long[1])})")
                lines.append("summary=" + ", ".join(summary_parts))
                lines += _render_table(
                    ["price", "zone", "total", "low≤10x", "med11-25x", "high26-50x", "degen>50x"],
                    [
                        [
                            _fmt_num(price, use_suffix=False),
                            "SHORT_PAIN" if price >= median_price else "LONG_PAIN",
                            _fmt_num(vol),
                            _fmt_num(by_price_lev[price].get("low", 0)),
                            _fmt_num(by_price_lev[price].get("med", 0)),
                            _fmt_num(by_price_lev[price].get("high", 0)),
                            _fmt_num(by_price_lev[price].get("degen", 0)),
                        ]
                        for price, vol in top_levels
                    ],
                )
                return _truncate(lines, total_items=len(by_price_total), shown_items=len(top_levels))

        # Fallback: flat levels without leverage breakdown
        levels = _extract_depth_levels(data)
        if not levels:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        half = TOP_N // 2
        top_above = levels[:half]
        if levels:
            median_price = sorted([p for p, _ in levels])[len(levels) // 2]
            below_levels = sorted(
                [(p, v) for p, v in levels if p < median_price],
                key=lambda x: x[1], reverse=True
            )[:half]
            top_levels = top_above + below_levels
            top_levels = sorted(top_levels, key=lambda x: x[0], reverse=True)
        else:
            top_levels = top_above

        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["price_level", "liq_volume"],
            [[_fmt_num(price, use_suffix=False), _fmt_num(vol)] for price, vol in top_levels],
        )
        return _truncate(lines, total_items=len(levels), shown_items=len(top_levels))

    if action in {"coin_map", "pair_map"}:
        rows = _extract_liq_map_rows(data)
        if not rows:
            rows = _records(data)
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        normalized: list[dict[str, Any]] = []
        for r in rows:
            long_v = _as_float(_pick(r, "long_liq", "long", "buy", "longLiq")) or 0.0
            short_v = _as_float(_pick(r, "short_liq", "short", "sell", "shortLiq")) or 0.0
            total = long_v + short_v
            normalized.append({
                "price": _pick(r, "price", "level", "price_level"),
                "long": long_v,
                "short": short_v,
                "total": total,
            })

        selected = sorted(normalized, key=lambda x: x["total"], reverse=True)[:TOP_N]
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["price_level", "long_liq", "short_liq", "total"],
            [
                [
                    _fmt_num(item["price"], use_suffix=False),
                    _fmt_num(item["long"]),
                    _fmt_num(item["short"]),
                    _fmt_num(item["total"]),
                ]
                for item in selected
            ],
        )
        return _truncate(lines, total_items=len(normalized), shown_items=len(selected))

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_ob_history(action: str, data: Any) -> str:
    tool = "coinglass_ob_history"

    if action in {"pair_depth", "coin_depth"}:
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "bid_usd", "ask_usd", "bid_ask_ratio"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(
                    _pick(
                        r,
                        "aggregated_bids_usd",
                        "bids_usd",
                        "bid_usd",
                        "bid",
                        "bidVolume",
                        "bid_depth",
                    )
                ),
                _fmt_num(
                    _pick(
                        r,
                        "aggregated_asks_usd",
                        "asks_usd",
                        "ask_usd",
                        "ask",
                        "askVolume",
                        "ask_depth",
                    )
                ),
                _safe_ratio(
                    _pick(
                        r,
                        "aggregated_bids_usd",
                        "bids_usd",
                        "bid_usd",
                        "bid",
                        "bidVolume",
                        "bid_depth",
                    ),
                    _pick(
                        r,
                        "aggregated_asks_usd",
                        "asks_usd",
                        "ask_usd",
                        "ask",
                        "askVolume",
                        "ask_depth",
                    ),
                ),
            ],
            limit=5,
        )

    if action == "heatmap":
        levels = _extract_depth_levels(data)
        if not levels:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        selected = levels[:TOP_N]
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["price_level", "depth"],
            [[_fmt_num(price, use_suffix=False), _fmt_num(depth)] for price, depth in selected],
        )
        return _truncate(lines, total_items=len(levels), shown_items=len(selected))

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_ob_large_orders(action: str, data: Any) -> str:
    tool = "coinglass_ob_large_orders"
    if action not in {"current", "history", "large_orders", "legacy_current", "legacy_history"}:
        raise ValueError(f"No formatter action for {tool}:{action}")

    rows = _records(data)
    if not rows and isinstance(data, dict):
        rows = [data]
    if not rows:
        return _truncate([_header(tool, action, data), "No data returned"], None, None)

    normalized = []
    total_bid = 0.0
    total_ask = 0.0
    for r in rows:
        volume = _as_float(
            _pick(
                r,
                "current_usd_value",
                "start_usd_value",
                "executed_usd_value",
                "start_quantity",
                "current_quantity",
                "volume",
                "amount",
                "size",
                "order_value",
                "value",
            )
        ) or 0.0
        side_raw = str(_pick(r, "order_side", "side", "direction") or "").lower()
        if side_raw in {"2", "bid", "buy", "long"}:
            total_bid += volume
            side = "bid"
        elif side_raw in {"1", "ask", "sell", "short"}:
            total_ask += volume
            side = "ask"
        else:
            side = "-"

        normalized.append({
            "price": _pick(r, "limit_price", "price", "order_price", "trigger_price"),
            "volume": volume,
            "side": side,
            "time": _pick(r, "start_time", "current_time", "order_end_time", "time", "timestamp"),
        })

    top5 = sorted(normalized, key=lambda x: x["volume"], reverse=True)[:TOP_N]

    # Wall summary: nearest/largest bid and ask walls
    bids = [n for n in normalized if n["side"] == "bid" and n["volume"] > 0]
    asks = [n for n in normalized if n["side"] == "ask" and n["volume"] > 0]
    largest_bid = max(bids, key=lambda x: x["volume"]) if bids else None
    largest_ask = max(asks, key=lambda x: x["volume"]) if asks else None
    imbalance = total_bid / total_ask if total_ask > 0 else 0

    summary_parts = [
        f"count:{len(rows)}",
        f"total_bid:{_fmt_num(total_bid)}",
        f"total_ask:{_fmt_num(total_ask)}",
        f"imbalance:{imbalance:.2f}x {'bid' if imbalance >= 1 else 'ask'}-heavy",
    ]
    if largest_bid:
        summary_parts.append(f"largest_bid_wall:{_fmt_num(largest_bid['price'], use_suffix=False)}({_fmt_num(largest_bid['volume'])})")
    if largest_ask:
        summary_parts.append(f"largest_ask_wall:{_fmt_num(largest_ask['price'], use_suffix=False)}({_fmt_num(largest_ask['volume'])})")

    lines = [
        _header(tool, action, data),
        f"summary={', '.join(summary_parts)}",
    ]
    lines += _render_table(
        ["time", "price", "volume", "side"],
        [
            [
                _to_utc(item["time"]),
                _fmt_num(item["price"], use_suffix=False),
                _fmt_num(item["volume"]),
                item["side"],
            ]
            for item in top5
        ],
    )
    return _truncate(lines, total_items=len(rows), shown_items=len(top5))


def format_coinglass_whale_positions(action: str, data: Any) -> str:
    tool = "coinglass_whale_positions"

    if action == "alerts":
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "user", "symbol", "action", "position_usd"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                str(_pick(r, "user", "exchange", "exName") or "-"),
                str(_pick(r, "symbol", "coin") or "-"),
                str(_pick(r, "position_action", "side", "direction") or "-"),
                _fmt_num(_pick(r, "position_value_usd", "size", "notional", "value", "amount")),
            ],
        )

    if action in {"positions", "all_positions"}:
        return _format_generic_top(
            tool,
            action,
            data,
            ["user", "symbol", "size", "entry", "liq_price", "leverage", "position_usd", "pnl", "updated"],
            lambda r: [
                str(_pick(r, "user", "exchange", "exName") or "-"),
                str(_pick(r, "symbol", "coin") or "-"),
                _fmt_num(_pick(r, "position_size", "size", "notional", "value", "amount")),
                _fmt_num(_pick(r, "entry_price", "entry", "entryPrice"), use_suffix=False),
                _fmt_num(_pick(r, "liq_price", "liquidation_price"), use_suffix=False),
                str(_pick(r, "leverage") or "-"),
                _fmt_num(_pick(r, "position_value_usd", "notional", "value", "amount")),
                _fmt_num(_pick(r, "unrealized_pnl", "pnl", "profit")),
                _to_utc(_pick(r, "update_time", "updateTime")),
            ],
            sort_keys=("position_value_usd", "notional", "size", "value", "amount"),
        )

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_bitfinex_longs_shorts(action: str, data: Any) -> str:
    tool = "coinglass_bitfinex_longs_shorts"
    # Server uses action='bitfinex_margin'.
    if action not in {"bitfinex_margin"}:
        raise ValueError(f"No formatter action for {tool}:{action}")

    rows = _rows_from_array_points(data, ("time", "long_quantity", "short_quantity"))
    if not rows:
        rows = _as_timeseries_rows(data)
    if not rows:
        return _truncate([_header(tool, action, data), "No data returned"], None, None)

    selected = rows[-TIME_SERIES_N:]
    lines = [_header(tool, action, data)]
    lines += _render_table(
        ["time", "long_vol", "short_vol", "ratio"],
        [
            [
                _to_utc_seconds(_pick(r, "time", "timestamp", "t")),
                _fmt_num(_pick(r, "long_quantity", "longVolume", "long", "long_vol", "longs")),
                _fmt_num(_pick(r, "short_quantity", "shortVolume", "short", "short_vol", "shorts")),
                _safe_ratio(
                    _pick(r, "long_quantity", "longVolume", "long", "long_vol", "longs"),
                    _pick(r, "short_quantity", "shortVolume", "short", "short_vol", "shorts"),
                ),
            ]
            for r in selected
        ],
    )
    return _truncate(lines, total_items=len(rows), shown_items=len(selected))


def _cvd_summary(data: Any) -> str:
    """Calculate CVD (Cumulative Volume Delta) from taker buy/sell data."""
    rows = _records(data)
    if not rows:
        return ""
    cvd = 0.0
    positive_bars = 0
    negative_bars = 0
    for r in rows:
        buy = _as_float(_pick(r, "buy_vol_usd", "taker_buy_volume_usd", "aggregated_buy_volume_usd", "buy_volume", "buy")) or 0.0
        sell = _as_float(_pick(r, "sell_vol_usd", "taker_sell_volume_usd", "aggregated_sell_volume_usd", "sell_volume", "sell")) or 0.0
        delta = buy - sell
        cvd += delta
        if delta > 0:
            positive_bars += 1
        elif delta < 0:
            negative_bars += 1
    total = positive_bars + negative_bars
    direction = "BULLISH" if cvd > 0 else "BEARISH"
    streak_pct = (positive_bars / total * 100) if total > 0 else 0
    return f"cvd={_fmt_num(cvd)}, direction={direction}, buy_bars={positive_bars}/{total}({streak_pct:.0f}%)"


def format_coinglass_taker(action: str, data: Any) -> str:
    tool = "coinglass_taker"

    if action in {"coin_history", "pair_history"}:
        base = _format_last_points(
            tool,
            action,
            data,
            ["time", "buy_vol", "sell_vol", "buy_pct"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(
                    _pick(
                        r,
                        "buy_vol_usd",
                        "taker_buy_volume_usd",
                        "aggregated_buy_volume_usd",
                        "buy_volume",
                        "buyVol",
                        "buy",
                        "buy_usd",
                    )
                ),
                _fmt_num(
                    _pick(
                        r,
                        "sell_vol_usd",
                        "taker_sell_volume_usd",
                        "aggregated_sell_volume_usd",
                        "sell_volume",
                        "sellVol",
                        "sell",
                        "sell_usd",
                    )
                ),
                _fmt_pct(
                    (
                        (_as_float(_pick(
                            r,
                            "buy_vol_usd",
                            "taker_buy_volume_usd",
                            "aggregated_buy_volume_usd",
                            "buy_volume",
                            "buyVol",
                            "buy",
                            "buy_usd",
                        )) or 0.0)
                        / max(
                            (
                                (_as_float(_pick(
                                    r,
                                    "buy_vol_usd",
                                    "taker_buy_volume_usd",
                                    "aggregated_buy_volume_usd",
                                    "buy_volume",
                                    "buyVol",
                                    "buy",
                                    "buy_usd",
                                )) or 0.0)
                                + (_as_float(_pick(
                                    r,
                                    "sell_vol_usd",
                                    "taker_sell_volume_usd",
                                    "aggregated_sell_volume_usd",
                                    "sell_volume",
                                    "sellVol",
                                    "sell",
                                    "sell_usd",
                                )) or 0.0)
                            ),
                            1e-12,
                        )
                    ),
                    ratio_input=True,
                ),
            ],
        )
        # Append CVD summary
        cvd = _cvd_summary(data)
        if cvd:
            lines = base.split("\n")
            lines.insert(1, cvd)
            return "\n".join(lines)
        return base

    if action == "by_exchange":
        rows: list[dict[str, Any]] = []
        if isinstance(data, dict) and isinstance(data.get("exchange_list"), list):
            symbol = _pick(data, "symbol")
            for item in data["exchange_list"]:
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                if symbol is not None:
                    row.setdefault("symbol", symbol)
                rows.append(row)
        if not rows:
            rows = _records(data)

        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        selected = _top(rows, sort_keys=("buy_vol_usd", "sell_vol_usd", "buy_volume", "sell_volume"), limit=TOP_N)
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["exchange", "buy_vol", "sell_vol", "ratio"],
            [
                [
                    str(_pick(r, "exchange", "exchange_name", "exName", "key") or "-"),
                    _fmt_num(_pick(r, "buy_vol_usd", "buy_volume", "buyVol", "buy")),
                    _fmt_num(_pick(r, "sell_vol_usd", "sell_volume", "sellVol", "sell")),
                    _fmt_num(_pick(r, "buy_ratio", "ratio", "buy_sell_ratio", "buySellRatio"), use_suffix=False),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    if action == "aggregated_ratio":
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "buy_vol", "sell_vol", "ratio"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(
                    _pick(
                        r,
                        "aggregated_buy_volume_usd",
                        "taker_buy_volume_usd",
                        "buy_vol_usd",
                        "buy_volume",
                        "buyVol",
                        "buy",
                    )
                ),
                _fmt_num(
                    _pick(
                        r,
                        "aggregated_sell_volume_usd",
                        "taker_sell_volume_usd",
                        "sell_vol_usd",
                        "sell_volume",
                        "sellVol",
                        "sell",
                    )
                ),
                _fmt_num(
                    (
                        (_as_float(
                            _pick(
                                r,
                                "aggregated_buy_volume_usd",
                                "taker_buy_volume_usd",
                                "buy_vol_usd",
                                "buy_volume",
                                "buyVol",
                                "buy",
                            )
                        ) or 0.0)
                        / max(
                            (
                                (_as_float(
                                    _pick(
                                        r,
                                        "aggregated_buy_volume_usd",
                                        "taker_buy_volume_usd",
                                        "buy_vol_usd",
                                        "buy_volume",
                                        "buyVol",
                                        "buy",
                                    )
                                ) or 0.0)
                                + (_as_float(
                                    _pick(
                                        r,
                                        "aggregated_sell_volume_usd",
                                        "taker_sell_volume_usd",
                                        "sell_vol_usd",
                                        "sell_volume",
                                        "sellVol",
                                        "sell",
                                    )
                                ) or 0.0)
                            ),
                            1e-12,
                        )
                    ),
                    use_suffix=False,
                ),
            ],
        )

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_spot(action: str, data: Any) -> str:
    tool = "coinglass_spot"

    if action in {"coins_markets", "pairs_markets"}:
        return _format_generic_top(
            tool,
            action,
            data,
            ["pair", "exchange", "price", "chg_24h", "volume"],
            lambda r: [
                str(_pick(r, "symbol", "pair") or "-"),
                str(_pick(r, "exchange_name", "exchange", "exName") or "-"),
                _fmt_num(_pick(r, "current_price", "price", "close", "c"), use_suffix=False),
                _fmt_pct(_pick(r, "price_change_percent_24h", "change_24h", "change24h")),
                _fmt_num(
                    total_volume
                    if (
                        total_volume := _as_float(
                            _pick(r, "volume_usd_24h", "volume_usd", "volume_24h", "volume24h")
                        )
                    )
                    is not None
                    else (
                        (_as_float(_pick(r, "buy_volume_usd_24h", "buy_volume_usd", "buy_volume")) or 0.0)
                        + (_as_float(_pick(r, "sell_volume_usd_24h", "sell_volume_usd", "sell_volume")) or 0.0)
                    )
                ),
            ],
            sort_keys=("volume_usd_24h", "volume_usd", "volume_24h", "volume24h"),
        )

    return _format_default(tool, action, data, limit=TIME_SERIES_N)


def format_coinglass_options(action: str, data: Any) -> str:
    tool = "coinglass_options"

    if action == "info":
        rows = _records(data)
        if not rows and isinstance(data, dict):
            rows = [data]
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        selected = _top(
            rows,
            sort_keys=("open_interest", "open_interest_usd", "volume_usd_24h", "volume"),
            limit=TOP_N,
        )
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["exchange", "oi", "volume", "chg_24h"],
            [
                [
                    str(_pick(r, "exchange_name", "exchange", "exName") or "-"),
                    _fmt_num(_pick(r, "open_interest", "open_interest_usd", "oi", "openInterest")),
                    _fmt_num(_pick(r, "volume_usd_24h", "volume", "vol")),
                    _fmt_pct(_pick(r, "open_interest_change_24h", "change_24h", "change24h", "oi_change_24h")),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    if action == "max_pain":
        rows = _records(data)
        if not rows and isinstance(data, dict):
            rows = [data]
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        # Calculate aggregate P/C ratio from all expiries
        total_call = sum(_as_float(_pick(r, "call_open_interest", "call_oi", "callOi")) or 0 for r in rows)
        total_put = sum(_as_float(_pick(r, "put_open_interest", "put_oi", "putOi")) or 0 for r in rows)
        pc_ratio = f"{total_put / total_call:.2f}" if total_call > 0 else "-"

        selected = _top(rows, sort_keys=("date", "expiry_time", "expiry"), limit=TOP_N)
        lines = [_header(tool, action, data)]
        lines.append(f"summary=put_call_ratio:{pc_ratio}, total_call:{_fmt_num(total_call)}, total_put:{_fmt_num(total_put)}")
        lines += _render_table(
            ["date", "max_pain", "call_oi", "put_oi", "call_notional", "put_notional"],
            [
                [
                    str(_pick(r, "date", "expiry") or "-"),
                    _fmt_num(_pick(r, "max_pain_price", "maxPainPrice"), use_suffix=False),
                    _fmt_num(_pick(r, "call_open_interest", "call_oi", "callOi")),
                    _fmt_num(_pick(r, "put_open_interest", "put_oi", "putOi")),
                    _fmt_num(_pick(r, "call_open_interest_notional", "call_notional")),
                    _fmt_num(_pick(r, "put_open_interest_notional", "put_notional")),
                ]
                for r in selected
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    if action == "volume_history":
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "volume", "call_vol", "put_vol"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(_pick(r, "volume", "vol", "total")),
                _fmt_num(_pick(r, "call_volume", "call_vol", "callVol")),
                _fmt_num(_pick(r, "put_volume", "put_vol", "putVol")),
            ],
        )

    if action == "oi_history":
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "oi", "call_oi", "put_oi"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                _fmt_num(_pick(r, "open_interest", "oi", "openInterest")),
                _fmt_num(_pick(r, "call_open_interest", "call_oi", "callOi")),
                _fmt_num(_pick(r, "put_open_interest", "put_oi", "putOi")),
            ],
        )

    raise ValueError(f"No formatter action for {tool}:{action}")


def format_coinglass_onchain(action: str, data: Any) -> str:
    tool = "coinglass_onchain"

    if action == "balance_list":
        return _format_generic_top(
            tool,
            action,
            data,
            ["exchange", "balance", "chg_24h"],
            lambda r: [
                str(_pick(r, "exchange_name", "exchange", "exName", "key") or "-"),
                _fmt_num(_pick(r, "total_balance", "balance", "value", "amount")),
                _fmt_pct(
                    _pick(
                        r,
                        "balance_change_percent_1d",
                        "balance_change_1d",
                        "change_24h",
                        "change24h",
                        "balance_change_24h",
                    )
                ),
            ],
            sort_keys=("total_balance", "balance", "value", "amount"),
        )

    if action == "whale_transfer":
        return _format_last_points(
            tool,
            action,
            data,
            ["time", "from", "to", "amount", "tx"],
            lambda r: [
                _to_utc(_find_time_value(r)),
                str(_pick(r, "from", "from_address", "fromAddress") or "-"),
                str(_pick(r, "to", "to_address", "toAddress") or "-"),
                _fmt_num(_pick(r, "amount", "value", "usd", "amount_usd")),
                str(_pick(r, "tx", "tx_hash", "hash") or "-"),
            ],
        )

    return _format_default(tool, action, data, limit=TIME_SERIES_N)


def format_coinglass_etf(action: str, data: Any) -> str:
    tool = "coinglass_etf"

    if action in {"flows", "bitcoin_flows", "ethereum_flows", "solana_flows"}:
        rows = _records(data)
        if not rows and isinstance(data, dict):
            rows = [data]
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        sorted_rows = sorted(
            rows,
            key=lambda r: _to_epoch_seconds(_pick(r, "timestamp", "time", "date"))
            or float("-inf"),
        )
        selected = sorted_rows[-5:]

        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["date", "flow_usd", "price_usd"],
            [
                [
                    _to_utc(_pick(r, "timestamp", "time", "date")),
                    _fmt_num(_pick(r, "flow_usd", "flow", "net_flow", "netFlow")),
                    _fmt_num(_pick(r, "price_usd", "price"), use_suffix=False),
                ]
                for r in selected
            ],
        )

        ticker_rows: list[tuple[str, str, float]] = []
        for day in selected:
            day_label = _to_utc(_pick(day, "timestamp", "time", "date"))
            nested = day.get("etf_flows")
            if not isinstance(nested, list):
                continue
            for item in nested:
                if not isinstance(item, dict):
                    continue
                flow_usd = _as_float(_pick(item, "flow_usd", "flow")) or 0.0
                ticker_rows.append(
                    (
                        day_label,
                        str(_pick(item, "etf_ticker", "ticker", "symbol") or "-"),
                        flow_usd,
                    )
                )

        if ticker_rows:
            lines.append("per_etf:")
            lines += _render_table(
                ["date", "ticker", "flow_usd"],
                [
                    [date_label, ticker, _fmt_num(flow_usd)]
                    for date_label, ticker, flow_usd in sorted(
                        ticker_rows, key=lambda x: abs(x[2]), reverse=True
                    )[:10]
                ],
            )
        return _truncate(lines, total_items=len(rows), shown_items=len(selected))

    if action in {"list", "bitcoin_list", "ethereum_list"}:
        return _format_generic_top(
            tool,
            action,
            data,
            ["fund", "ticker", "assets", "flow_24h"],
            lambda r: [
                str(_pick(r, "fund", "name", "issuer") or "-"),
                str(_pick(r, "etf_ticker", "ticker", "symbol") or "-"),
                _fmt_num(_pick(r, "assets", "aum", "total_assets")),
                _fmt_num(_pick(r, "flow_usd", "flow_24h", "flow", "net_flow", "netFlow")),
            ],
            sort_keys=("assets", "aum", "total_assets"),
        )

    return _format_default(tool, action, data, limit=5)


def format_coinglass_indicators(action: str, data: Any) -> str:
    tool = "coinglass_indicators"

    if action == "fear_greed":
        rows: list[dict[str, Any]] = []
        if isinstance(data, dict):
            data_list = data.get("data_list")
            price_list = data.get("price_list")
            time_list = data.get("time_list")
            if (
                isinstance(data_list, list)
                and isinstance(price_list, list)
                and isinstance(time_list, list)
            ):
                for ts, value, price in zip(time_list, data_list, price_list):
                    rows.append({"time": ts, "value": value, "price_usd": price})
        if not rows:
            rows = _records(data)
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        current = rows[-1]
        current_value = _pick(current, "value", "fear_greed", "index", "data")
        current_class = _pick(current, "classification", "class") or _classify_fear_greed(current_value)
        lines = [
            _header(tool, action, data),
            (
                "current="
                f"value:{_fmt_num(current_value, use_suffix=False)}, "
                f"classification:{current_class}"
            ),
        ]
        tail = rows[-7:]
        lines += _render_table(
            ["time", "value", "price_usd", "classification"],
            [
                [
                    str(_pick(r, "date") or _to_utc(_find_time_value(r))),
                    _fmt_num(_pick(r, "value", "fear_greed", "index", "data"), use_suffix=False),
                    _fmt_num(_pick(r, "price_usd", "price", "btc_price"), use_suffix=False),
                    str(
                        _pick(r, "classification", "class")
                        or _classify_fear_greed(_pick(r, "value", "fear_greed", "index", "data"))
                    ),
                ]
                for r in tail
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(tail))

    if action == "rsi":
        return _format_generic_top(
            tool,
            action,
            data,
            ["symbol", "rsi_1h", "rsi_4h", "rsi_24h"],
            lambda r: [
                str(_pick(r, "symbol", "coin") or "-"),
                _fmt_num(_pick(r, "rsi_1h", "rsi", "value"), use_suffix=False),
                _fmt_num(_pick(r, "rsi_4h") or "-", use_suffix=False),
                _fmt_num(_pick(r, "rsi_24h") or "-", use_suffix=False),
            ],
            sort_keys=("rsi_1h", "rsi", "value"),
        )

    if action == "ahr999":
        rows = _as_timeseries_rows(data)
        if not rows:
            return _truncate([_header(tool, action, data), "No data returned"], None, None)

        tail = rows[-TIME_SERIES_N:]
        lines = [_header(tool, action, data)]
        lines += _render_table(
            ["date", "ahr999", "current_value", "average_price"],
            [
                [
                    str(_pick(r, "date_string", "date") or _to_utc(_find_time_value(r))),
                    _fmt_num(_pick(r, "ahr999_value", "ahr999", "value"), use_suffix=False),
                    _fmt_num(_pick(r, "current_value", "current", "price"), use_suffix=False),
                    _fmt_num(_pick(r, "average_price", "avg_price", "average"), use_suffix=False),
                ]
                for r in tail
            ],
        )
        return _truncate(lines, total_items=len(rows), shown_items=len(tail))

    rows = _as_timeseries_rows(data)
    if not rows:
        return _format_default(tool, action, data, limit=TIME_SERIES_N)

    tail = rows[-TIME_SERIES_N:]

    # Multi-value indicators keep all major sub-values.
    if action in {"futures_macd", "futures_boll"}:
        keys = sorted(
            k
            for k in {kk for row in tail for kk in row.keys()}
            if k not in {"t", "ts", "time", "timestamp", "date", "datetime"}
        )[:6]
        headers = ["time"] + keys
        table_rows = []
        for row in tail:
            table_rows.append([
                _to_utc(_find_time_value(row)),
                *[
                    _fmt_num(row.get(key), use_suffix=False)
                    if _as_float(row.get(key)) is not None
                    else str(row.get(key) if row.get(key) is not None else "-")
                    for key in keys
                ],
            ])
        lines = [_header(tool, action, data)] + _render_table(headers, table_rows)
        return _truncate(lines, total_items=len(rows), shown_items=len(tail))

    # Default single-value indicator.
    return _format_last_points(
        tool,
        action,
        data,
        ["time", "value"],
        lambda r: [
            _to_utc(_find_time_value(r)),
            _fmt_num(_pick(r, "value", "c", "close", "index"), use_suffix=False),
        ],
    )


def format_coinglass_config(action: str, data: Any) -> str:
    return _format_passthrough("coinglass_config", action, data)
