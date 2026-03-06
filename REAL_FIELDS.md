# REAL CoinGlass API v4 Response Fields (sampled live)

## oi_distribution (list of dicts)
exchange, open_interest_usd, open_interest_change_percent_24h
Sort by: open_interest_usd desc

## etf_flows (list of dicts)  
timestamp (ms), flow_usd, price_usd, etf_flows[].etf_ticker, etf_flows[].flow_usd
Show last 5 by timestamp desc

## options_info (list of dicts)
exchange_name, open_interest, open_interest_usd, open_interest_change_24h, volume_usd_24h, volume_change_percent_24h
Sort by open_interest desc

## options_max_pain (list of dicts)
date, max_pain_price, call_open_interest, put_open_interest, call_open_interest_notional, put_open_interest_notional

## fear_greed (dict with PARALLEL ARRAYS - NOT list of dicts!)
data_list: [30.0, 15.0, ...] (fear/greed values)
price_list: [10114.49, ...] (BTC prices)
time_list: [...] (timestamps)
MUST zip these together! Last 7 items.

## long_short_global (list of dicts)
time (ms), global_account_long_percent, global_account_short_percent, global_account_long_short_ratio

## liq_aggregated
Returns list of dicts with: time, long_volUsd, short_volUsd (check exact field names - may return empty)

## heatmap (dict with sparse matrix)
y_axis: [1875.721, 1878.138, ...] (price levels)
liquidation_leverage_data: [[y_idx, x_timestamp, value], ...] (sparse entries)
Aggregate by y_axis price → sum values → top 10 clusters

## ob_large_orders (list of dicts)
id, exchange_name, symbol, limit_price, start_usd_value, current_usd_value, order_side (1=ASK, 2=BID), order_state
Sort by current_usd_value desc, show top 5

## whale_alerts (list of dicts)  
user, symbol, position_size, entry_price, liq_price, position_value_usd, position_action, create_time (ms)

## bitfinex (list of dicts)
time (SECONDS not ms!), long_quantity, short_quantity

## ob_depth (list of dicts)
aggregated_bids_usd, aggregated_asks_usd, aggregated_bids_quantity, aggregated_asks_quantity, time (ms)

## taker_exchange (dict with nested list)
symbol, buy_ratio, sell_ratio, buy_vol_usd, sell_vol_usd
exchange_list[].exchange, exchange_list[].buy_ratio, exchange_list[].sell_ratio, exchange_list[].buy_vol_usd, exchange_list[].sell_vol_usd

## onchain_balance (list of dicts)
exchange_name, total_balance, balance_change_1d, balance_change_percent_1d

## max_pain_liq (list of dicts)
symbol, price, long_max_pain_liq_level, long_max_pain_liq_price, short_max_pain_liq_level, short_max_pain_liq_price

## coins_markets (list of dicts, 74 fields - key ones)
symbol, current_price, price_change_percent_1h, price_change_percent_4h, price_change_percent_24h
open_interest_usd, open_interest_change_percent_24h, market_cap_usd
avg_funding_rate_by_oi, liquidation_usd_24h, long_liquidation_usd_24h, short_liquidation_usd_24h
