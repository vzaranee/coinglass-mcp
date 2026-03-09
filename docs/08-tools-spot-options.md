# Tools: Spot, Options, On-Chain

## Overview

These tools provide access to spot markets, options analytics, and on-chain exchange data.

| Tool | Description |
|------|-------------|
| `coinglass_spot` | Spot market data |
| `coinglass_options` | Options market analytics |
| `coinglass_onchain` | On-chain exchange data |

---

## coinglass_spot

Get spot market data.

### Actions

| Action | Description | Endpoint | Required Params |
|--------|-------------|----------|-----------------|
| `coins` | Supported coins | `/api/spot/supported-coins` | - |
| `pairs` | Exchange pairs (supports `exchange` and `symbol` filters) | `/api/spot/supported-exchange-pairs` | - |
| `coins_markets` | Coin data | `/api/spot/coins-markets` | - |
| `pairs_markets` | Pair data | `/api/spot/pairs-markets` | `symbol` |
| `price_history` | OHLC | `/api/price/ohlc-history` | exchange, pair, interval |

### Parameters

```python
ActionSpot = Literal["coins", "pairs", "coins_markets", "pairs_markets", "price_history"]

@mcp.tool
async def coinglass_spot(
    action: Annotated[ActionSpot, Field(
        description="coins: supported | pairs: exchange pairs (filterable by exchange/symbol) | coins_markets: coin data | pairs_markets: pair data (requires symbol) | price_history: OHLC"
    )],
    symbol: Annotated[str | None, Field(description="Coin/base-asset filter; required for pairs_markets")] = None,
    exchange: Annotated[str | None, Field(description="Exchange filter; required for price_history")] = None,
    pair: Annotated[str | None, Field(description="Pair for price_history")] = None,
    interval: Annotated[str | None, Field(description="For price_history: h1, h4, d1")] = None,
    limit: Annotated[int, Field(ge=1, le=4500)] = 500,
    ctx: Context
) -> dict:
```

### Examples

```python
# Get all spot coins
coinglass_spot(action="coins")

# Spot market data for BTC
coinglass_spot(action="coins_markets", symbol="BTC")

# Spot pairs filtered by exchange + base asset
coinglass_spot(action="pairs", exchange="Binance", symbol="BTC")

# Spot price history
coinglass_spot(
    action="price_history",
    exchange="Binance",
    pair="BTCUSDT",
    interval="h1"
)
```

### Response (coins_markets)

Runtime responses return compact `text` plus preview/truncation metadata (`truncated`,
`requested_limit`, `shown_rows`, `total_rows`/`total_known`, `filters_applied`,
`truncation_reason`).

```json
{
  "success": true,
  "action": "coins_markets",
  "data": [
    {
      "symbol": "BTC",
      "price": 97500.5,
      "price_change_24h": 2.35,
      "volume_24h_usd": 28000000000,
      "market_cap_usd": 1920000000000
    }
  ],
  "metadata": {...}
}
```

---

## coinglass_options

Get options market data (Deribit, OKX, Binance, Bybit).

### Actions

| Action | Description | Endpoint |
|--------|-------------|----------|
| `max_pain` | Max pain price | `/api/option/max-pain` |
| `info` | OI/volume summary | `/api/option/info` |
| `oi_history` | OI over time | `/api/option/exchange-oi-history` |
| `volume_history` | Volume over time | `/api/option/exchange-vol-history` |

### Parameters

```python
ActionOptions = Literal["max_pain", "info", "oi_history", "volume_history"]

@mcp.tool
async def coinglass_options(
    action: Annotated[ActionOptions, Field(
        description="max_pain: max pain price | info: OI/volume summary | oi_history: OI over time | volume_history: volume over time"
    )],
    symbol: Annotated[Literal["BTC", "ETH"], Field(description="BTC or ETH only")],
    range: Annotated[str | None, Field(description="For history: 7d, 30d, 90d")] = None,
    ctx: Context
) -> dict:
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `action` | Literal | Yes | - | Options data type |
| `symbol` | Literal | Yes | - | BTC or ETH only |
| `range` | str | No | None | Time range for history |

### Examples

```python
# BTC max pain
coinglass_options(action="max_pain", symbol="BTC")

# ETH options info
coinglass_options(action="info", symbol="ETH")

# BTC OI history
coinglass_options(action="oi_history", symbol="BTC", range="30d")
```

### Response (max_pain)

```json
{
  "success": true,
  "action": "max_pain",
  "data": {
    "symbol": "BTC",
    "max_pain_price": 95000,
    "current_price": 97500.5,
    "distance_pct": -2.56,
    "expiration_dates": [
      {
        "date": "2025-12-06",
        "max_pain": 94000,
        "call_oi": 15000,
        "put_oi": 12000
      }
    ]
  },
  "metadata": {...}
}
```

### Response (info)

```json
{
  "success": true,
  "action": "info",
  "data": {
    "symbol": "BTC",
    "total_oi_usd": 18500000000,
    "total_volume_24h_usd": 2800000000,
    "put_call_ratio": 0.72,
    "exchanges": [
      {
        "exchange": "Deribit",
        "oi_usd": 12500000000,
        "volume_usd": 1850000000,
        "share_pct": 67.6
      }
    ]
  },
  "metadata": {...}
}
```

### Cache TTL
2 minutes

---

## coinglass_onchain

Get on-chain exchange data.

### Actions

| Action | Description | Endpoint | Required Params |
|--------|-------------|----------|-----------------|
| `assets` | Exchange holdings | `/api/exchange/assets` | - |
| `balance_list` | Balances by asset | `/api/exchange/balance/list` | - |
| `balance_chart` | Historical balances | `/api/exchange/balance/chart` | asset, exchange |
| `transfers` | ERC-20 transactions | `/api/exchange/chain/tx/list` | - |

### Parameters

```python
ActionOnChain = Literal["assets", "balance_list", "balance_chart", "transfers"]

@mcp.tool
async def coinglass_onchain(
    action: Annotated[ActionOnChain, Field(
        description="assets: exchange holdings (requires exchange) | balance_list: balances by asset | balance_chart: historical | transfers: ERC-20 txs"
    )],
    exchange: Annotated[str | None, Field(description="Exchange filter (required for assets)")] = None,
    asset: Annotated[str | None, Field(description="Asset: BTC, ETH, USDT")] = None,
    range: Annotated[str | None, Field(description="For balance_chart: 7d, 30d, 90d")] = None,
    transfer_type: Annotated[Literal["inflow", "outflow", "internal"] | None, Field(description="Filter transfers")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ctx: Context
) -> dict:
```

### Examples

```python
# All exchange assets
coinglass_onchain(action="assets", exchange="Binance")

# BTC balances across exchanges
coinglass_onchain(action="balance_list", asset="BTC")

# Historical BTC balance on Binance
coinglass_onchain(
    action="balance_chart",
    asset="BTC",
    exchange="Binance",
    range="30d"
)

# Recent inflow transfers
coinglass_onchain(action="transfers", transfer_type="inflow", limit=20)
```

### Response (assets)

```json
{
  "success": true,
  "action": "assets",
  "data": [
    {
      "exchange": "Binance",
      "assets": [
        {
          "asset": "BTC",
          "balance": 585000,
          "balance_usd": 57037500000,
          "change_24h": 1250,
          "change_pct_24h": 0.21
        },
        {
          "asset": "ETH",
          "balance": 4250000,
          "balance_usd": 14875000000,
          "change_24h": -15000,
          "change_pct_24h": -0.35
        }
      ]
    }
  ],
  "metadata": {...}
}
```

### Response (balance_chart)

```json
{
  "success": true,
  "action": "balance_chart",
  "data": [
    {
      "timestamp": "2025-12-01T00:00:00Z",
      "balance": 585000,
      "balance_usd": 57037500000
    }
  ],
  "metadata": {
    "asset": "BTC",
    "exchange": "Binance",
    "range": "30d"
  }
}
```

### Response (transfers)

```json
{
  "success": true,
  "action": "transfers",
  "data": [
    {
      "exchange": "Binance",
      "asset": "USDT",
      "flow_type": "inflow",
      "amount": 15000000,
      "amount_usd": 15000000,
      "tx_hash": "0xabc...123",
      "from_address": "0x111...222",
      "to_address": "0x333...444",
      "timestamp": "2025-12-01T11:45:00Z"
    }
  ],
  "metadata": {
    "transfer_type": "inflow",
    "total_records": 20
  }
}
```

### Cache TTL
- `assets`: 5 minutes
- `balance_list`: 5 minutes
- `balance_chart`: 10 minutes
- `transfers`: 1 minute
