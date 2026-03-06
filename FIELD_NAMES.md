# Real CoinGlass API v4 Field Names

## oi_distribution (exchange-list)
exchange, open_interest_usd, open_interest_change_percent_24h

## funding_rates (exchange-list)  
symbol, stablecoin_margin_list[].exchange, stablecoin_margin_list[].funding_rate, stablecoin_margin_list[].next_funding_time

## liq_by_exchange
exchange, liquidation_usd, longLiquidation_usd, shortLiquidation_usd

## max_pain
symbol, price, long_max_pain_liq_level, long_max_pain_liq_price, short_max_pain_liq_level, short_max_pain_liq_price

## options_info
exchange_name, open_interest, open_interest_usd, open_interest_change_24h, volume_usd_24h

## etf_flows
timestamp, flow_usd, price_usd, etf_flows[]

## fear_greed
data_list[], price_list[], time_list[] (parallel arrays, not objects!)

## coins_markets (74 fields, key ones)
symbol, current_price, price_change_percent_1h/4h/24h, open_interest_usd, open_interest_change_percent_24h, market_cap_usd, avg_funding_rate_by_oi, volume_change_percent_24h, liquidation_usd_24h, long_liquidation_usd_24h, short_liquidation_usd_24h

## taker_exchange
symbol, buy_ratio, sell_ratio, buy_vol_usd, sell_vol_usd, exchange_list[].exchange/buy_ratio/sell_ratio/buy_vol_usd/sell_vol_usd

## ob_large_orders (large-limit-order)
id, exchange_name, symbol, limit_price, start_quantity, start_usd_value, current_quantity, current_usd_value, order_side (1=ASK/SELL above price, 2=BID/BUY below price), order_state

## onchain_balance
exchange_name, total_balance, balance_change_1d, balance_change_percent_1d

## long_short (global/top_accounts/top_positions)
Returns list of [timestamp, longRate, shortRate, longShortRatio] (arrays, not dicts!)

## liq_history (aggregated)
Returns parallel arrays: dateList[], longList[], shortList[] (not objects!)

## heatmap (aggregated-heatmap/model1)
y_axis: [prices], liquidation_leverage_data: [[y_idx, x_timestamp, value], ...] (sparse matrix!)

## liq_map (aggregated-map)
data: dict where keys are price strings, values are [[leverage, volume], ...]

## whale_positions
exchange, symbol, longShortRatio, longRate, shortRate, timestamp

## bitfinex_longs_shorts
Returns list of [timestamp, longVolume, shortVolume] (arrays, not dicts!)

## ob_history (aggregated-ask-bids-history)
aggregated_bids_usd, aggregated_asks_usd, aggregated_bids_quantity, aggregated_asks_quantity, time
