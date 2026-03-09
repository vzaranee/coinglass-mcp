# Tools: Market Data

## Overview

Market data tools provide access to static metadata and real-time market information.

| Tool | Description |
|------|-------------|
| `coinglass_market_info` | Static metadata (coins, pairs, exchanges) |
| `coinglass_market_data` | Real-time market summaries |
| `coinglass_price_history` | Historical OHLC price data |

---

## coinglass_market_info

Get static market metadata from CoinGlass.

### Actions

| Action | Description | Endpoint |
|--------|-------------|----------|
| `coins` | List of all supported futures coins | `/api/futures/supported-coins` |
| `pairs` | Trading pairs by exchange | `/api/futures/supported-exchange-pairs` |
| `exchanges` | List of supported exchanges | (derived from pairs) |

### Parameters

```python
ActionMarketInfo = Literal["coins", "pairs", "exchanges"]

@mcp.tool
async def coinglass_market_info(
    action: Annotated[ActionMarketInfo, Field(
        description="coins: supported coins | pairs: exchange pairs | exchanges: list exchanges"
    )],
    exchange: Annotated[str | None, Field(description="Filter by exchange")] = None,
    ctx: Context
) -> dict:
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | Literal | Yes | Operation to perform |
| `exchange` | str | No | Filter results by exchange |

### Examples

```python
# Get all supported coins
coinglass_market_info(action="coins")

# Get pairs for Binance
coinglass_market_info(action="pairs", exchange="Binance")

# List all exchanges
coinglass_market_info(action="exchanges")
```

### Response

```json
{
  "success": true,
  "action": "coins",
  "data": ["BTC", "ETH", "SOL", "XRP", ...],
  "metadata": {
    "level": "raw",
    "cached": true,
    "timestamp": "2025-12-01T12:00:00Z",
    "total_records": 150
  }
}
```

### Cache TTL
5 minutes (static data)

---

## coinglass_market_data

Get real-time market data summaries.

### Actions

| Action | Description | Endpoint |
|--------|-------------|----------|
| `coins_summary` | Price, OI, volume, funding for all coins | `/api/futures/coins-markets` |
| `pairs_summary` | Per-pair market metrics | `/api/futures/pairs-markets` |
| `price_changes` | Price % changes across timeframes | `/api/futures/price-change-list` |

### Parameters

```python
ActionMarketData = Literal["coins_summary", "pairs_summary", "price_changes"]

@mcp.tool
async def coinglass_market_data(
    action: Annotated[ActionMarketData, Field(
        description="coins_summary: single-coin metrics (requires symbol) | pairs_summary: pair metrics (defaults to BTC when symbol omitted) | price_changes: price % changes"
    )],
    symbol: Annotated[str | None, Field(description="Required for coins_summary; optional for other actions")] = None,
    ctx: Context
) -> dict:
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | Literal | Yes | Operation to perform |
| `symbol` | str | Action-dependent | `coins_summary`: required. Other actions: optional |

### Examples

```python
# Get BTC market summary
coinglass_market_data(action="coins_summary", symbol="BTC")

# Get all price changes
coinglass_market_data(action="price_changes")
```

### Response Structure

Runtime responses return compact `text` plus machine-readable `metadata` fields such as
`truncated`, `requested_limit`, `shown_rows`, `total_rows`/`total_known`,
`filters_applied`, and `truncation_reason`.

#### coins_summary
```json
{
  "success": true,
  "action": "coins_summary",
  "data": [
    {
      "symbol": "BTC",
      "price": 97500.5,
      "price_change_24h": 2.35,
      "open_interest_usd": 25000000000,
      "oi_change_24h": 1.2,
      "volume_24h_usd": 45000000000,
      "funding_rate": 0.0085
    }
  ],
  "metadata": {
    "level": "normalized",
    "cached": false,
    "timestamp": "2025-12-01T12:00:00Z"
  }
}
```

#### price_changes
```json
{
  "success": true,
  "action": "price_changes",
  "data": [
    {
      "symbol": "BTC",
      "price": 97500.5,
      "change_5m": 0.15,
      "change_15m": 0.25,
      "change_30m": 0.45,
      "change_1h": 0.85,
      "change_4h": 1.20,
      "change_24h": 2.35,
      "change_7d": 5.50
    }
  ],
  "metadata": {...}
}
```

### Cache TTL
30 seconds

---

## coinglass_price_history

Get historical price OHLC data for a specific trading pair.

### Parameters

```python
@mcp.tool
async def coinglass_price_history(
    exchange: Annotated[str, Field(description="Exchange: Binance, OKX, Bybit")],
    pair: Annotated[str, Field(description="Trading pair: BTCUSDT, ETHUSDT")],
    interval: Annotated[str, Field(description="Interval: m1, m5, m15, m30, h1, h4, d1")],
    limit: Annotated[int, Field(ge=1, le=4500)] = 500,
    start_time: Annotated[int | None, Field(description="Start timestamp ms")] = None,
    end_time: Annotated[int | None, Field(description="End timestamp ms")] = None,
    ctx: Context
) -> dict:
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `exchange` | str | Yes | - | Exchange name |
| `pair` | str | Yes | - | Trading pair |
| `interval` | str | Yes | - | Candle interval |
| `limit` | int | No | 500 | Number of candles (max 4500) |
| `start_time` | int | No | None | Start timestamp in milliseconds |
| `end_time` | int | No | None | End timestamp in milliseconds |

### Valid Intervals

| Interval | Description |
|----------|-------------|
| `m1` | 1 minute |
| `m5` | 5 minutes |
| `m15` | 15 minutes |
| `m30` | 30 minutes |
| `h1` | 1 hour |
| `h4` | 4 hours |
| `d1` | 1 day |

### Examples

```python
# Get hourly BTC price history
coinglass_price_history(
    exchange="Binance",
    pair="BTCUSDT",
    interval="h1",
    limit=100
)

# Get daily ETH price history
coinglass_price_history(
    exchange="OKX",
    pair="ETHUSDT",
    interval="d1",
    limit=30
)
```

### Response

```json
{
  "success": true,
  "action": "price_history",
  "data": [
    {
      "timestamp": "2025-12-01T11:00:00Z",
      "open": 97200.5,
      "high": 97650.0,
      "low": 97100.0,
      "close": 97500.5,
      "volume": 15000.5
    }
  ],
  "metadata": {
    "level": "normalized",
    "cached": false,
    "timestamp": "2025-12-01T12:00:00Z",
    "symbol": "BTCUSDT",
    "exchange": "Binance",
    "interval": "h1",
    "total_records": 100
  }
}
```

### Cache TTL
2 minutes

### Plan Restrictions

| Plan | Minimum Interval |
|------|------------------|
| Hobbyist | h4 |
| Startup | m30 |
| Standard+ | m1 |
