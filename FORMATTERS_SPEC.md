# Response Formatters Spec

## Problem
MCP tools return raw CoinGlass JSON — up to 1.5MB per call. LLMs can't process this efficiently.
Proven compression: 723-3410x (2MB raw → 2.9KB formatted).

## Architecture
- New file: `src/coinglass_mcp/formatters.py`
- Each tool gets a formatter function: `format_{tool_name}(action: str, data: Any) -> str`
- `ok()` in server.py calls formatter before returning text
- If no formatter exists for a tool/action, fall back to `json.dumps(data)[:2000]` with truncation warning

## Formatter Pattern
```python
def format_market_data(action: str, data: Any) -> str:
    if action == "coins_summary":
        # Extract: symbol, price, price_change_24h, oi, volume_24h, market_cap
        # Return: compact table or key-value pairs
        ...
    elif action == "pairs_summary":
        # Top 10 pairs by volume, with price/change/oi
        ...
```

## What Each Formatter Should Extract

### coinglass_market_data
- coins_summary: price, 1h/4h/24h change, OI, volume, mcap, funding_rate
- pairs_summary: TOP 10 by volume — pair, exchange, price, change_24h, OI, volume
- price_changes: symbol, timeframe changes
- volume_footprint: buy/sell volume, ratio

### coinglass_oi_history
- aggregated/pair/stablecoin/coin_margin: last 5-10 candles — time, OI value, OI change%

### coinglass_oi_distribution
- by_exchange: exchange, OI_usd, OI_change_24h%, percentage — sorted by OI desc
- exchange_chart: last 5 time points per exchange (top 5 exchanges)

### coinglass_funding_current
- rates: For given symbol — exchange, funding_rate, next_funding_time. Sorted by rate. Show avg, min, max
- accumulated: symbol, accumulated rate per exchange over range
- arbitrage: top 10 opportunities by spread

### coinglass_funding_history
- pair/oi_weighted/vol_weighted: last 10 candles — time, rate value

### coinglass_long_short
- global/top_accounts/top_positions/taker_ratio: last 10 candles — time, long%, short%, ratio
- net_position*: last 10 — time, net_long, net_short

### coinglass_liq_history
- aggregated: last 10 candles — time, long_liq, short_liq, total
- by_coin: TOP 10 coins by liquidation — symbol, long_liq, short_liq, total, long%
- by_exchange: exchanges sorted by liq — exchange, liq_usd, long%, short%
- max_pain: long_max_pain_price, long_max_pain_level, short_max_pain_price, short_max_pain_level

### coinglass_liq_orders
- Aggregate by price range (buckets): price_range, long_liq_volume, short_liq_volume

### coinglass_liq_heatmap
- coin_heatmap/pair_heatmap: TOP 10 price clusters by liquidation volume (aggregate y_axis + leverage data)
- coin_map/pair_map: price levels with long/short liq volumes, sorted by total desc, top 10

### coinglass_ob_history
- coin_depth/pair_depth: last 5 candles — time, bid_usd, ask_usd, bid/ask ratio
- heatmap: top 10 price levels by depth

### coinglass_ob_large_orders
- current: count, total_bid_volume, total_ask_volume, top 5 largest orders (price, volume, side)
- history: summary stats + top 5

### coinglass_taker
- coin_history/pair_history: last 10 candles — time, buy_vol, sell_vol, buy%
- by_exchange: exchange, buy_vol, sell_vol, ratio — sorted by total desc
- aggregated_ratio: last 10 — time, ratio

### coinglass_spot
- coins_markets/pairs_markets: TOP 10 by volume — same as futures equivalent
- Others: last 10 data points

### coinglass_options
- info: exchange, OI, volume, change_24h
- max_pain: expiry dates with max_pain_price, call_oi, put_oi, pcr
- volume_history: last 10 — time, volume, call_vol, put_vol
- oi_history: last 10 — time, oi, call_oi, put_oi

### coinglass_whale_positions
- positions: TOP 10 by notional — exchange, symbol, side, size, entry, pnl
- alerts: last 10 alerts — time, exchange, symbol, side, size
- all_positions: TOP 10

### coinglass_etf
- flows/bitcoin_flows/ethereum_flows/solana_flows: last 5 days — date, flow_usd, total_assets
- list/bitcoin_list/ethereum_list: fund, ticker, assets, flow_24h
- Others: last 5 data points

### coinglass_indicators
- fear_greed: current value + last 7 days
- Most indicators: last 10 candles — time, value
- For multi-value indicators (boll, macd): include all sub-values

### coinglass_onchain
- balance_list: exchange, balance, change_24h — top 10 by balance
- whale_transfer: last 10 transfers — time, from, to, amount, tx
- Others: appropriate summary

### coinglass_bitfinex_longs_shorts
- Last 10 candles: time, long_vol, short_vol, ratio

### coinglass_grayscale
- holdings: fund, shares, nav, premium
- premium: last 10 — time, premium%

### coinglass_config, coinglass_search, coinglass_article, coinglass_calendar, coinglass_market_info
- Pass through as-is (small responses, no formatting needed)

## Rules
1. Always include a header line: `{tool}({action}) | {symbol if present} | {timestamp}`
2. Numbers: round to 2 decimals, use K/M/B suffixes for large numbers
3. Percentages: 2 decimal places
4. Timestamps: convert epoch ms to `YYYY-MM-DD HH:MM` UTC
5. If data is None/empty, return "No data returned"
6. Max output: 3000 chars. If longer, truncate with "... (N more items)"
7. Use `|` separated columns for tabular data (not markdown tables)
