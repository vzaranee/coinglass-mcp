# CoinGlass MCP — Architecture Roadmap

## Current State (v1, March 2026)
- 23 mega-tools with Literal action params (facade pattern)
- Text formatters truncate API data (74 fields → ~10 shown)
- Client-side symbol filtering hacks (RSI, whale, coins_markets)
- Hardcoded exchange/symbol lists partially replaced with API calls
- 145 API endpoints behind 23 tool facades

## Planned Improvements

### 1. Granular Tools
Split mega-tools into individual MCP tools (1 tool ≈ 1 API endpoint).
LLMs select better between distinct tools than between action values within one tool.

**Before:** `coinglass_oi_distribution(action="by_exchange"|"exchange_chart"|"coin_margin"|...)`
**After:** `coinglass_oi_by_exchange(symbol)`, `coinglass_oi_exchange_chart(symbol, range)`, etc.

### 2. Enum in JSON Schema
Put exchanges, intervals, symbols directly into JSON Schema `enum` fields.
Client sees allowed values from `tools/list` without calling `config(exchanges)` first.

```python
# Before
exchange: str = Field(description="Exchange name (e.g., 'Binance')")

# After
exchange: Literal["Binance","OKX","Bybit",...] = Field(description="Exchange")
```

### 3. Structured JSON Output
Return structured JSON instead of formatted text tables.
Formatting is the client's job, not the server's.

**Before:** `"price=1964.37\noi=25.27B, chg_1h:0.31%"`
**After:** `{"price": 1964.37, "oi": {"value": 25270000000, "change_1h_pct": 0.31}}`

### 4. Server-Side Filtering
Remove client-side symbol filter hacks (RSI, whale_positions, coins_markets).
Either the API filters, or the server caches full data and filters efficiently.

### 5. MCP Resources for Static Data
Exchange lists, supported coins, intervals → MCP Resources (cacheable), not Tools.
Resources are designed for relatively static, read-only data.

```python
@server.resource("coinglass://exchanges")
async def list_exchanges():
    ...
```

### 6. Consistent Parameter Naming
Unify `symbol` vs `pair` vs `exchange` vs `exchange_list` across all tools.
One convention, documented, no surprises.

| Current | Proposed |
|---------|----------|
| `symbol="BTC"` | `symbol="BTC"` (coin) |
| `pair="BTCUSDT"` | `pair="BTCUSDT"` (trading pair) |
| `exchange="Binance"` | `exchange="Binance"` (single) |
| `exchange_list="Binance"` | `exchange="Binance"` (drop `_list`) |

## Priority
- Items 1-2 have highest impact on LLM usability
- Item 3 unblocks richer client-side analysis
- Items 4-6 are cleanup/consistency
