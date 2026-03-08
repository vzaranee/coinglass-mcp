# CODEX.md — Project Context for Codex

## Architecture
- **FastMCP 3.1** SSE server, Python 3
- 23 tools (facade pattern: 1 tool = multiple actions via Literal)
- `server.py` → tool handlers → `client.py` (HTTP) → CoinGlass API v4
- `formatters.py` → compact text output for LLM consumption

## Critical Patterns

### Tool return format
Tools MUST return `dict`, not `str`. FastMCP 3.1 rejects bare strings.
```python
return {"text": formatted_string}  # ✅
return formatted_string             # ❌ structured_content must be dict
```

### ok() wrapper
`ok(action, data)` → calls `formatters.format_tool_response(tool_name, action, data)` → returns `{"text": ...}`.
Do NOT return raw `data` dict with a `data` key — it leaks unformatted JSON.

### _pick() for safe field access
```python
_pick(row, "field_name", "fallback_name")  # case-insensitive, returns first match
```
Always use `_pick()` in formatters, never raw `row["field"]`.

### _truncate() for output limits
MAX_OUTPUT_CHARS = 4000, FALLBACK_JSON_CHARS = 3000, TOP_N = 15, TIME_SERIES_N = 24.

## CoinGlass API v4 Quirks
- **Field naming**: aggregated endpoints prefix with `aggregated_` (e.g. `aggregated_long_liquidation_usd`, not `long_liquidation_usd`)
- **RSI**: returns `rsi_1h`, `rsi_4h`, `rsi_12h`, `rsi_24h` (not bare `rsi`)
- **whale-position**: use `update_time` for freshness, `create_time` = position open date
- **Heatmap 3D**: `liquidation_leverage_data` = `[y_index, leverage_bucket, volume]` array
- **Options info**: no call/put split — P/C ratio comes from `max_pain` action
- **exchange_list** (not `exchange`) for coin_margin and stablecoin OI
- **large_orders**: `order_side` 1=bid, 2=ask; `state` is int (0/1/2)
- **camelCase paths often 404** — use kebab-case
- **`range`** for heatmap (3d/7d/30d), not `interval`

## Timestamps
API returns mixed formats: unix seconds, unix milliseconds, ISO strings, None.
Always normalize before display or sort. Show `"-"` if absent, never `datetime.now()`.

## Testing
After any change: `python3 -c "from src.coinglass_mcp import server, client, formatters; print('OK')"`

## Output
Save reviews/reports to project root or `/mnt/storage/codex-reviews/`.
