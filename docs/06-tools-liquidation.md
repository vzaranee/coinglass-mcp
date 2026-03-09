# Tools: Liquidation & Order Book

## Overview

Liquidation and order book tools provide insights into market liquidations and depth.

| Tool | Description | Min Plan |
|------|-------------|----------|
| `coinglass_liq_history` | Liquidation history data | Hobbyist |
| `coinglass_liq_orders` | Real-time liquidation orders | Standard |
| `coinglass_liq_heatmap` | Heatmap visualizations | Professional |
| `coinglass_ob_history` | Order book depth history | Hobbyist |
| `coinglass_ob_large_orders` | Large limit orders (whale walls) | Hobbyist |

---

## coinglass_liq_history

Get liquidation history data.

### Actions

| Action | Description | Endpoint | Required Params |
|--------|-------------|----------|-----------------|
| `pair` | Single pair | `/api/futures/liquidation/history` | exchange, pair |
| `aggregated` | By coin | `/api/futures/liquidation/aggregated-history` | symbol |
| `by_coin` | Coin summary | `/api/futures/liquidation/coin-list` | - |
| `by_exchange` | Exchange summary | `/api/futures/liquidation/exchange-list` | - |

### Parameters

```python
ActionLiqHistory = Literal["pair", "aggregated", "by_coin", "by_exchange"]

@mcp.tool
async def coinglass_liq_history(
    action: Annotated[ActionLiqHistory, Field(
        description="pair: single pair | aggregated: by coin | by_coin: coin summary | by_exchange: exchange summary"
    )],
    symbol: Annotated[str | None, Field(description="Coin for aggregated/by_coin")] = None,
    exchange: Annotated[str | None, Field(description="Exchange for pair action")] = None,
    pair: Annotated[str | None, Field(description="Trading pair for pair action")] = None,
    interval: Annotated[str, Field(description="m5, h1, h4, h12, d1")] = "h1",
    limit: Annotated[int, Field(ge=1, le=4500)] = 500,
    ctx: Context
) -> dict:
```

### Examples

```python
# Aggregated BTC liquidations
coinglass_liq_history(action="aggregated", symbol="BTC", interval="h1")

# Specific pair liquidations
coinglass_liq_history(action="pair", exchange="Binance", pair="BTCUSDT")

# Summary by coin
coinglass_liq_history(action="by_coin")
```

### Response

```json
{
  "success": true,
  "action": "aggregated",
  "data": [
    {
      "timestamp": "2025-12-01T11:00:00Z",
      "long_usd": 15000000,
      "short_usd": 8500000,
      "total_usd": 23500000,
      "long_count": 1250,
      "short_count": 890
    }
  ],
  "metadata": {
    "symbol": "BTC",
    "interval": "h1"
  }
}
```

---

## coinglass_liq_orders

Get real-time liquidation orders stream.

### Plan Requirement
**Standard+ plan required**

### Parameters

```python
@mcp.tool
async def coinglass_liq_orders(
    symbol: Annotated[str | None, Field(description="Filter by coin")] = None,
    side: Annotated[Literal["long", "short"] | None, Field(description="Filter by side")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ctx: Context
) -> dict:
```

### Examples

```python
# All recent liquidations
coinglass_liq_orders()

# BTC long liquidations only
coinglass_liq_orders(symbol="BTC", side="long")
```

### Response

```json
{
  "success": true,
  "action": "orders",
  "data": [
    {
      "exchange": "Binance",
      "pair": "BTCUSDT",
      "side": "long",
      "price": 97250.5,
      "quantity": 0.85,
      "value_usd": 82662.93,
      "timestamp": "2025-12-01T11:59:45Z"
    }
  ],
  "metadata": {
    "cached": false,
    "total_records": 50
  }
}
```

### Cache TTL
5 seconds

---

## coinglass_liq_heatmap

Get liquidation heatmap/map visualization data.

### Plan Requirement
**Professional+ plan required**

### Actions

| Action | Description | Endpoint |
|--------|-------------|----------|
| `pair_heatmap` | Pair visualization | `/api/futures/liquidation/heatmap/model{1,2,3}` |
| `coin_heatmap` | Aggregated | `/api/futures/liquidation/aggregated-heatmap/model{1,2,3}` |
| `pair_map` | Leverage levels | `/api/futures/liquidation/map` |
| `coin_map` | Aggregated levels | `/api/futures/liquidation/aggregated-map` |

### Parameters

```python
ActionLiqHeatmap = Literal["pair_heatmap", "coin_heatmap", "pair_map", "coin_map"]

@mcp.tool
async def coinglass_liq_heatmap(
    action: Annotated[ActionLiqHeatmap, Field(
        description="pair_heatmap: pair visualization | coin_heatmap: aggregated | pair_map: leverage levels | coin_map: aggregated levels"
    )],
    symbol: Annotated[str | None, Field(description="Coin for coin_* actions")] = None,
    exchange: Annotated[str | None, Field(description="Exchange for pair_* actions")] = None,
    pair: Annotated[str | None, Field(description="Trading pair for pair_* actions")] = None,
    range: Annotated[str, Field(description="3d, 7d, 14d, 30d, 90d, 180d, 1y")] = "7d",
    model: Annotated[Literal[1, 2, 3], Field(description="Heatmap model (1-3)")] = 1,
    ctx: Context
) -> dict:
```

### Models

| Model | Description |
|-------|-------------|
| 1 | Basic liquidation levels |
| 2 | Enhanced with volume weighting |
| 3 | Advanced with order flow |

### Examples

```python
# BTC aggregated heatmap
coinglass_liq_heatmap(action="coin_heatmap", symbol="BTC", range="7d", model=2)

# Specific pair leverage map
coinglass_liq_heatmap(action="pair_map", exchange="Binance", pair="BTCUSDT")
```

### Response

```json
{
  "success": true,
  "action": "coin_heatmap",
  "data": {
    "price_levels": [...],
    "liquidation_intensity": [...],
    "current_price": 97500.5
  },
  "metadata": {
    "symbol": "BTC",
    "range": "7d",
    "model": 2
  }
}
```

---

## coinglass_ob_history

Get order book depth history.

### Actions

| Action | Description | Endpoint |
|--------|-------------|----------|
| `pair_depth` | Pair bid/ask | `/api/futures/orderbook/ask-bids-history` |
| `coin_depth` | Aggregated | `/api/futures/orderbook/aggregated-ask-bids-history` |
| `heatmap` | Orderbook heatmap | `/api/futures/orderbook/history` |

### Parameters

```python
ActionOBHistory = Literal["pair_depth", "coin_depth", "heatmap"]

@mcp.tool
async def coinglass_ob_history(
    action: Annotated[ActionOBHistory, Field(
        description="pair_depth: pair bid/ask | coin_depth: aggregated | heatmap: orderbook heatmap"
    )],
    symbol: Annotated[str | None, Field(description="Coin for coin_depth")] = None,
    exchange: Annotated[str | None, Field(description="Exchange")] = None,
    pair: Annotated[str | None, Field(description="Trading pair")] = None,
    interval: Annotated[str, Field(description="m5, m15, h1, h4")] = "h1",
    range: Annotated[str, Field(description="1, 2, 5 (% from mid price)")] = "2",
    market: Annotated[Literal["futures", "spot"], Field(description="Market type")] = "futures",
    limit: Annotated[int, Field(ge=1, le=4500)] = 500,
    ctx: Context
) -> dict:
```

### Examples

```python
# Pair order book depth
coinglass_ob_history(
    action="pair_depth",
    exchange="Binance",
    pair="BTCUSDT",
    range="2"
)

# Aggregated coin depth
coinglass_ob_history(action="coin_depth", symbol="BTC")
```

### Response

```json
{
  "success": true,
  "action": "pair_depth",
  "data": [
    {
      "timestamp": "2025-12-01T11:00:00Z",
      "bid_amount": 125000000,
      "ask_amount": 118000000,
      "bid_ask_ratio": 1.059
    }
  ],
  "metadata": {
    "exchange": "Binance",
    "pair": "BTCUSDT",
    "range": "2%"
  }
}
```

---

## coinglass_ob_large_orders

Get large limit orders (whale walls).

### Actions

| Action | Description | Endpoint |
|--------|-------------|----------|
| `current` | Active large orders (requires `symbol` or `pair`) | `/api/futures/orderbook/large-limit-order` |
| `history` | Historical large orders (requires `exchange`, `symbol`/`pair`, `start_time`, `end_time`, `state`) | `/api/futures/orderbook/large-limit-order-history` |

### Thresholds
- BTC: ≥$1M
- ETH: ≥$500K
- Others: ≥$50K

### Parameters

```python
ActionLargeOrders = Literal["current", "history"]

@mcp.tool
async def coinglass_ob_large_orders(
    action: Annotated[ActionLargeOrders, Field(
        description="current: active large orders (requires symbol/pair) | history: historical large orders (requires exchange,symbol,start_time,end_time,state)"
    )],
    exchange: Annotated[str | None, Field(description="Filter by exchange")] = None,
    pair: Annotated[str | None, Field(description="Filter by pair")] = None,
    market: Annotated[Literal["futures", "spot"], Field(description="Market type")] = "futures",
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ctx: Context
) -> dict:
```

### Examples

```python
# Current whale walls
coinglass_ob_large_orders(action="current", symbol="BTCUSDT")

# BTC large orders on Binance
coinglass_ob_large_orders(action="current", exchange="Binance", pair="BTCUSDT")

# Historical large orders
coinglass_ob_large_orders(action="history", limit=100)
```

### Response

Runtime responses return compact `text` plus preview/truncation metadata fields
(`truncated`, `shown_rows`, `total_rows`, `requested_limit`, `truncation_reason`).

```json
{
  "success": true,
  "action": "current",
  "data": [
    {
      "exchange": "Binance",
      "pair": "BTCUSDT",
      "side": "bid",
      "price": 95000.0,
      "amount_usd": 2500000,
      "amount_btc": 26.32,
      "timestamp": "2025-12-01T10:30:00Z"
    }
  ],
  "metadata": {
    "cached": false,
    "total_records": 50
  }
}
```

### Cache TTL
- `current`: 10 seconds
- `history`: 1 minute
