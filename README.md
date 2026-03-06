# CoinGlass MCP Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastMCP 3.1+](https://img.shields.io/badge/FastMCP-3.1+-green.svg)](https://github.com/jlowin/fastmcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-51%20passed-brightgreen.svg)](#testing)

> MCP server for [CoinGlass](https://www.coinglass.com) cryptocurrency derivatives analytics. Provides AI agents access to **143 API endpoints** through **26 unified tools** with built-in response formatting.

## What It Does

CoinGlass is the go-to platform for crypto derivatives data — open interest, funding rates, liquidations, whale positions, ETF flows, on-chain metrics, and more. This MCP server wraps their entire API v4 into a set of tools that any MCP-compatible AI agent (Claude, OpenClaw, Cursor, etc.) can use directly.

**Key difference from raw API access:** Every response is compressed through smart formatters — a 2MB JSON blob becomes a 2KB structured summary. Your AI agent gets the signal, not the noise.

---

## Features

- **26 MCP Tools → 143 Endpoints** — Facade pattern keeps tool count low for LLM context efficiency
- **Response Formatters** — Raw API JSON (up to 2MB) → structured text summaries (~1-3KB)
- **Server-Side Symbol Filtering** — Endpoints that return all coins are filtered to your requested symbol
- **Plan-Aware Gating** — Automatic feature restrictions based on your CoinGlass subscription tier
- **SSE & stdio Transport** — Works with Claude Desktop (stdio) and remote setups (SSE)
- **Retry Logic** — Automatic retries with backoff for transient API failures
- **Type-Safe Actions** — `Literal`-typed actions help LLMs pick the right operation

---

## Quick Start

### 1. Get a CoinGlass API Key

Sign up at [coinglass.com/pricing](https://www.coinglass.com/pricing). Free tier works but has limited endpoints.

### 2. Install

```bash
# From PyPI
pip install coinglass-mcp

# Or from source
git clone https://github.com/vzaranee/coinglass-mcp.git
cd coinglass-mcp
pip install -e .
```

### 3. Configure

```bash
export COINGLASS_API_KEY="your-api-key"
export COINGLASS_PLAN="professional"  # hobbyist | startup | standard | professional | enterprise
```

### 4. Run

```bash
# stdio transport (for Claude Desktop / local agents)
coinglass-mcp

# SSE transport (for remote access / OpenClaw / mcporter)
coinglass-mcp --transport sse --port 8100
```

---

## Claude Desktop Integration

Add to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "coinglass": {
      "command": "coinglass-mcp",
      "env": {
        "COINGLASS_API_KEY": "your-api-key",
        "COINGLASS_PLAN": "professional"
      }
    }
  }
}
```

Restart Claude Desktop. You'll see the CoinGlass tools in the 🔨 menu.

### Using with Claude Desktop

Ask Claude things like:

> "What's the current funding rate for ETH across exchanges?"

> "Show me BTC liquidation heatmap for the last 24 hours"

> "Find funding rate arbitrage opportunities"

> "Compare open interest distribution for SOL across exchanges"

Claude will automatically select the right CoinGlass tool and action.

---

## Claude Web (claude.ai) Integration

Claude Web supports MCP servers through the **Integrations** menu:

1. Go to [claude.ai](https://claude.ai) → Settings → **Integrations**
2. Click **Add Integration** → **MCP Server**
3. Enter connection details:
   - **Name:** `CoinGlass`
   - **Transport:** SSE
   - **URL:** Your server URL (e.g., `http://your-server:8100/sse`)
4. Save and start a new conversation

> **Note:** Claude Web requires your MCP server to be accessible over the network (not localhost). Use a VPS, tunnel (ngrok/cloudflared), or deploy with Docker.

### Expose via Cloudflare Tunnel (recommended)

```bash
# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# Quick tunnel (no Cloudflare account needed)
cloudflared tunnel --url http://localhost:8100

# Output: https://random-name.trycloudflare.com
# Use this URL + /sse in Claude Web integration
```

### Expose via ngrok

```bash
ngrok http 8100
# Use the https://xxx.ngrok.io URL + /sse in Claude Web
```

---

## Docker

### Quick Run

```bash
docker run -d \
  --name coinglass-mcp \
  -p 8100:8100 \
  -e COINGLASS_API_KEY=your-api-key \
  -e COINGLASS_PLAN=professional \
  ghcr.io/vzaranee/coinglass-mcp:latest
```

### Build from Source

```bash
git clone https://github.com/vzaranee/coinglass-mcp.git
cd coinglass-mcp

docker build -t coinglass-mcp .
docker run -d \
  --name coinglass-mcp \
  -p 8100:8100 \
  -e COINGLASS_API_KEY=your-api-key \
  -e COINGLASS_PLAN=professional \
  coinglass-mcp
```

### Docker Compose

```yaml
# docker-compose.yml
services:
  coinglass-mcp:
    build: .
    ports:
      - "8100:8100"
    environment:
      COINGLASS_API_KEY: ${COINGLASS_API_KEY}
      COINGLASS_PLAN: professional
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8100/sse"]
      interval: 30s
      timeout: 5s
      retries: 3
```

```bash
# Start
COINGLASS_API_KEY=your-key docker compose up -d

# Check logs
docker compose logs -f coinglass-mcp
```

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8100

CMD ["coinglass-mcp", "--transport", "sse", "--port", "8100"]
```

---

## Systemd Service (Linux VPS)

For persistent deployment without Docker:

```bash
# /etc/systemd/system/coinglass-mcp.service
[Unit]
Description=CoinGlass MCP Server
After=network.target

[Service]
Type=simple
Environment=COINGLASS_API_KEY=your-api-key
Environment=COINGLASS_PLAN=professional
ExecStart=/usr/local/bin/coinglass-mcp --transport sse --port 8100
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coinglass-mcp
sudo systemctl status coinglass-mcp
```

---

## All 26 Tools & 143 Actions

### Market Data

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_market_info` | `coins`, `pairs`, `exchanges`, `supported_exchanges`, `delisted_pairs` | Supported coins, trading pairs, exchange lists |
| `coinglass_market_data` | `coins_summary`, `pairs_summary`, `price_changes`, `volume_footprint` | Real-time market overviews and volume analysis |
| `coinglass_price_history` | *(single action)* | OHLC price candles by pair/exchange |

### Open Interest

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_oi_history` | `pair`, `aggregated`, `stablecoin`, `coin_margin` | OI OHLC history by margin type |
| `coinglass_oi_distribution` | `by_exchange`, `exchange_chart` | OI breakdown across exchanges |

### Funding Rates

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_funding_history` | `pair`, `oi_weighted`, `vol_weighted` | Historical funding rate OHLC |
| `coinglass_funding_current` | `rates`, `accumulated`, `arbitrage` | Live rates, cumulative funding, arb opportunities |

### Long/Short Ratios

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_long_short` | `global`, `top_accounts`, `top_positions`, `taker_ratio`, `net_position`, `net_position_v2` | Account ratios, position ratios, taker L/S |

### Liquidations

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_liq_history` | `pair`, `aggregated`, `by_coin`, `by_exchange`, `max_pain` | Liquidation OHLC, aggregated stats, max pain levels |
| `coinglass_liq_orders` | *(single action)* | Real-time liquidation stream ⚡ |
| `coinglass_liq_heatmap` | `pair_heatmap`, `coin_heatmap`, `pair_map`, `coin_map` | Liquidation density maps 🔥 |

### Order Book

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_ob_history` | `pair_depth`, `coin_depth`, `heatmap` | Historical bid/ask depth and OB heatmaps |
| `coinglass_ob_large_orders` | `current`, `history`, `large_orders`, `legacy_current`, `legacy_history` | Whale wall detection — large limit orders |

### Whale Activity

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_whale_positions` | `alerts`, `positions`, `all_positions` | Hyperliquid whale alerts and positions ⚡ |

### Taker Buy/Sell

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_bitfinex_longs_shorts` | *(single action)* | Bitfinex margin longs vs shorts |
| `coinglass_taker` | `pair_history`, `coin_history`, `by_exchange`, `aggregated_ratio` | Taker buy/sell volume and ratio |

### Spot Markets

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_spot` | `coins`, `pairs`, `coins_markets`, `pairs_markets`, `price_history`, `taker_history`, `taker_aggregated_history`, `orderbook_*` (6 actions), `volume_footprint_history` | Full spot market data suite (13 actions) |

### Options

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_options` | `max_pain`, `info`, `oi_history`, `volume_history` | Options analytics (BTC/ETH only) |

### On-Chain

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_onchain` | `assets`, `balance_list`, `balance_chart`, `transfers`, `whale_transfer`, `assets_transparency` | Exchange balances, flows, whale transfers |

### ETF & Grayscale

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_etf` | `list`, `flows`, `history`, `net_assets`, `premium`, `detail`, `price`, `bitcoin_*` (7), `ethereum_*` (3), `solana_flows`, `xrp_flows`, `hk_bitcoin_flows` | Bitcoin/Ethereum/Solana/XRP ETF flows and data (20 actions) |
| `coinglass_grayscale` | `holdings`, `premium` | Grayscale fund holdings and NAV premium |

### Technical Indicators

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_indicators` | `rsi`, `futures_rsi`, `futures_ma`, `futures_ema`, `futures_macd`, `futures_boll`, `basis`, `coinbase_premium`, `fear_greed`, `ahr999`, `puell`, `stock_flow`, `pi_cycle`, `rainbow`, `bubble`, `altcoin_season`, `bitcoin_*` (13 metrics), `golden_ratio_multiplier`, `option_vs_futures_oi_ratio`, `ma_2year`, `ma_200week`, `profitable_days`, `stablecoin_mcap`, `bull_peak`, `borrow_rate`, `whale_index`, `cdri_index`, `cgdi_index` | 42 technical and macro indicators |

### Meta / Discovery

| Tool | Actions | Description |
|------|---------|-------------|
| `coinglass_article` | `list` | CoinGlass news articles |
| `coinglass_calendar` | `central_bank_activities`, `economic_data`, `financial_events` | Economic calendar |
| `coinglass_search` | *(query param)* | Discover tools by keyword |
| `coinglass_config` | `exchanges`, `intervals`, `rate_limits`, `plan_features` | Server configuration and plan info |

> ⚡ Requires Startup+ plan | 🔥 Requires Professional+ plan

---

## Usage Examples

### Crypto Screening (multi-coin)

```
# Get market overview for top coins
coinglass_market_data(action="coins_summary")

# Compare funding rates across coins
coinglass_funding_current(action="rates", symbol="ETH")

# Check liquidation heatmap for potential squeezes
coinglass_liq_heatmap(action="coin_heatmap", symbol="SOL")
```

### Deep Dive (single coin)

```
# Full picture for ETH
coinglass_oi_distribution(action="by_exchange", symbol="ETH")     # Who holds OI
coinglass_funding_current(action="rates", symbol="ETH")            # Current funding
coinglass_long_short(action="global", symbol="ETH")                # L/S ratio
coinglass_liq_heatmap(action="pair_heatmap", symbol="ETH")         # Where liquidations cluster
coinglass_ob_large_orders(action="current", symbol="ETH")          # Whale walls
coinglass_whale_positions(action="positions", symbol="ETH")        # Hyperliquid whales
coinglass_taker(action="coin_history", symbol="ETH")               # Buy/sell pressure
```

### Funding Arbitrage

```
# Find best arbitrage spreads
coinglass_funding_current(action="arbitrage")

# Check accumulated funding (who's paying more over time)
coinglass_funding_current(action="accumulated", symbol="BTC")
```

### ETF Flow Tracking

```
# Bitcoin ETF daily flows
coinglass_etf(action="bitcoin_flows")

# Ethereum ETF flows
coinglass_etf(action="ethereum_flows")

# Grayscale premium/discount
coinglass_grayscale(action="premium")
```

### Market Sentiment

```
# Fear & Greed index with price overlay
coinglass_indicators(action="fear_greed")

# Bitcoin Rainbow Chart
coinglass_indicators(action="rainbow")

# Altcoin Season Index
coinglass_indicators(action="altcoin_season")
```

---

## Response Format

All tools return formatted text summaries instead of raw JSON. Example:

```
# Input
coinglass_oi_distribution(action="by_exchange", symbol="ETH")

# Output (formatted, ~500 chars instead of ~50KB raw)
📊 ETH Open Interest by Exchange

Total OI: $25.7B (+2.3% 24h)

  Exchange        OI (USD)    Share   24h Chg
  Binance         $8.2B       31.9%   +3.1%
  Bybit           $5.1B       19.8%   +1.8%
  OKX             $4.3B       16.7%   +2.5%
  CME             $3.8B       14.8%   +4.2%
  Bitget          $1.9B        7.4%   +1.1%
  ...
```

This 723x compression (2MB → 2.8KB) keeps LLM context clean and costs low.

---

## Architecture

```
coinglass-mcp/
├── src/coinglass_mcp/
│   ├── server.py       # 26 MCP tools, 143 endpoint handlers (3163 lines)
│   ├── formatters.py   # Response compression — 28 formatters (2230 lines)
│   ├── client.py       # httpx client with retry logic
│   └── config.py       # Plan tiers, intervals, feature gates
├── tests/
│   └── test_tools.py   # 51 tests
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

**Design:**

- **Facade pattern** — 26 tools × multiple actions = 143 endpoints. Keeps LLM tool context small.
- **Literal-typed actions** — Each tool uses `Literal["action1", "action2", ...]` so LLMs see valid options in the schema.
- **Formatters layer** — Every API response passes through a dedicated formatter that extracts key metrics, ranks by relevance, and truncates to ~3KB max.
- **Server-side filtering** — Endpoints like `/funding-rate/exchange-list` return ALL coins. The server filters to your requested symbol before formatting.

---

## Plan Tiers

| Feature | Hobbyist | Startup | Standard | Professional | Enterprise |
|---------|:--------:|:-------:|:--------:|:------------:|:----------:|
| Basic intervals (h4, h8, d1) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Extended intervals (m1–h1) | ❌ | ✅ | ✅ | ✅ | ✅ |
| Whale alerts & positions | ❌ | ✅ | ✅ | ✅ | ✅ |
| Liquidation orders stream | ❌ | ❌ | ✅ | ✅ | ✅ |
| Liquidation heatmaps | ❌ | ❌ | ❌ | ✅ | ✅ |
| Rate limit (req/min) | 30 | 120 | 300 | 600 | 1200 |

---

## Development

```bash
git clone https://github.com/vzaranee/coinglass-mcp.git
cd coinglass-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest -v
# 51 passed ✅

# Run locally
export COINGLASS_API_KEY="your-key"
python -m coinglass_mcp.server
```

---

## Known API Limitations

These are CoinGlass API issues, not server bugs:

| Endpoint | Issue |
|----------|-------|
| `oi_distribution(exchange_chart)` | Server Error 500 |
| `options(oi_history)` | Server Error 500 |
| `liq_history(aggregated)` | Server Error 500 |
| `options(*)` | BTC and ETH only |
| `onchain(balance_list)` | No data for small-cap coins |

---

## API Reference

- [CoinGlass API v4 Docs](https://open-api.coinglass.com/)
- [CoinGlass Pricing](https://www.coinglass.com/pricing)
- [MCP Protocol Spec](https://modelcontextprotocol.io)
- [FastMCP Framework](https://github.com/jlowin/fastmcp)

---

## License

MIT
