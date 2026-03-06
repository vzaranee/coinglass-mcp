"""CoinGlass MCP Server - Cryptocurrency derivatives analytics.

This server provides 22 tools for accessing CoinGlass API data including:
- Market data (prices, coins, exchanges)
- Open Interest (OI) history and distribution
- Funding rates and arbitrage opportunities
- Long/Short ratios
- Liquidation data and heatmaps
- Order book depth and large orders
- Whale tracking on Hyperliquid
- Taker buy/sell volume
- Spot market data
- Options analytics
- On-chain exchange data
- ETF flows and holdings
- Market indicators (Fear & Greed, RSI, etc.)
"""

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastmcp import Context, FastMCP
from pydantic import Field

from coinglass_mcp.client import CoinGlassClient
from coinglass_mcp.config import (
    ACTION_PARAMS,
    ACTION_PLAN,
    PLAN_FEATURES,
    PLAN_HIERARCHY,
    PLAN_INTERVALS,
)


# ============================================================================
# SERVER SETUP
# ============================================================================


@asynccontextmanager
async def lifespan(mcp: FastMCP):
    """Initialize shared resources for server lifetime."""
    api_key = os.environ.get("COINGLASS_API_KEY", "")
    plan = os.environ.get("COINGLASS_PLAN", "standard").lower()

    if not api_key:
        raise ValueError(
            "COINGLASS_API_KEY environment variable is required. "
            "Get your API key at https://www.coinglass.com/pricing"
        )

    async with httpx.AsyncClient() as http:
        yield {
            "client": CoinGlassClient(http=http, api_key=api_key),
            "plan": plan,
        }


mcp = FastMCP(
    name="coinglass",
    instructions="""CoinGlass MCP - cryptocurrency derivatives analytics.

Access 80+ API endpoints for crypto market data including:
- Open Interest, Funding Rates, Liquidations
- Whale tracking, ETF flows, Market indicators

Common patterns:
- Market overview: coinglass_market_data(action="coins_summary")
- BTC open interest: coinglass_oi_history(action="aggregated", symbol="BTC")
- Funding rates: coinglass_funding_current(action="rates")
- Liquidations: coinglass_liq_history(action="aggregated", symbol="BTC")
- Whale activity: coinglass_whale_positions(action="positions")

Use coinglass_search(query="...") to discover available operations.""",
    lifespan=lifespan,
)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def get_client(ctx: Context) -> CoinGlassClient:
    """Get CoinGlass client from context."""
    if ctx is None:
        raise ValueError("Context is required - tool must be called via MCP")
    return ctx.request_context.lifespan_context["client"]


def get_plan(ctx: Context) -> str:
    """Get current plan from context."""
    if ctx is None:
        raise ValueError("Context is required - tool must be called via MCP")
    return ctx.request_context.lifespan_context["plan"]


def check_plan(ctx: Context, action: str) -> None:
    """Check if action is available for current plan.

    Raises:
        ValueError: If action requires higher plan
    """
    plan = get_plan(ctx)
    required = ACTION_PLAN.get(action)
    if required and PLAN_HIERARCHY.get(plan, 0) < PLAN_HIERARCHY.get(required, 0):
        raise ValueError(
            f"Action '{action}' requires {required} plan (current: {plan}). "
            f"Upgrade at https://www.coinglass.com/pricing"
        )


def check_interval(ctx: Context, interval: str) -> None:
    """Check if interval is available for current plan.

    Raises:
        ValueError: If interval requires higher plan
    """
    plan = get_plan(ctx)
    allowed = PLAN_INTERVALS.get(plan, set())
    if interval not in allowed:
        raise ValueError(
            f"Interval '{interval}' not available for {plan} plan. "
            f"Available intervals: {', '.join(sorted(allowed))}"
        )


def check_params(action: str, **kwargs: Any) -> None:
    """Check if required parameters are provided.

    Raises:
        ValueError: If required parameters are missing
    """
    required = ACTION_PARAMS.get(action, [])
    missing = [p for p in required if not kwargs.get(p)]
    if missing:
        raise ValueError(
            f"Action '{action}' requires parameters: {', '.join(missing)}"
        )


def ok(action: str, data: Any, **meta: Any) -> dict:
    """Create success response."""
    return {
        "success": True,
        "action": action,
        "data": data,
        "metadata": {k: v for k, v in meta.items() if v is not None},
    }


async def request_with_fallback(
    client: CoinGlassClient,
    endpoints: str | list[str],
    params: dict[str, Any] | None = None,
) -> Any:
    """Request endpoint(s), falling back on 404 for compatibility variants."""
    if isinstance(endpoints, str):
        return await client.request(endpoints, params)

    last_error: Exception | None = None
    for endpoint in endpoints:
        try:
            return await client.request(endpoint, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                last_error = exc
                continue
            raise

    if last_error:
        raise last_error
    raise ValueError("No endpoints provided for request fallback")


# ============================================================================
# MARKET TOOLS (3 tools)
# ============================================================================


ActionMarketInfo = Literal["coins", "pairs", "exchanges"]


@mcp.tool(
    name="coinglass_market_info",
    annotations={
        "title": "CoinGlass Market Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_market_info(
    action: Annotated[
        ActionMarketInfo,
        Field(
            description="coins: list supported coins | pairs: exchange trading pairs | exchanges: list exchanges"
        ),
    ],
    exchange: Annotated[
        str | None, Field(description="Filter by exchange (e.g., 'Binance', 'OKX')")
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get static market metadata from CoinGlass.

    Returns lists of supported coins, trading pairs by exchange, or exchanges.
    This data is relatively static and cached for 5 minutes.

    Examples:
        - Get all futures coins: action="coins"
        - Get Binance pairs: action="pairs", exchange="Binance"
        - List all exchanges: action="exchanges"
    """
    client = get_client(ctx)

    if action == "coins":
        data = await client.request("/api/futures/supported-coins")
        return ok(action, data, total=len(data) if isinstance(data, list) else None)

    elif action == "pairs":
        data = await client.request("/api/futures/supported-exchange-pairs")
        if exchange and isinstance(data, dict):
            data = {exchange: data.get(exchange, [])}
        return ok(action, data, exchange=exchange)

    else:  # exchanges
        data = await client.request("/api/futures/supported-exchange-pairs")
        exchanges = list(data.keys()) if isinstance(data, dict) else []
        return ok(action, exchanges, total=len(exchanges))


ActionMarketData = Literal["coins_summary", "pairs_summary", "price_changes"]


@mcp.tool(
    name="coinglass_market_data",
    annotations={
        "title": "CoinGlass Market Data",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_market_data(
    action: Annotated[
        ActionMarketData,
        Field(
            description="coins_summary: single coin metrics (requires symbol) | pairs_summary: per-pair metrics | price_changes: price % changes across timeframes"
        ),
    ],
    symbol: Annotated[
        str | None,
        Field(description="Coin symbol - REQUIRED for coins_summary (e.g., 'BTC', 'ETH')"),
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get real-time market data summaries from CoinGlass.

    Returns aggregated market metrics including price, open interest, volume,
    and funding rates. Data is updated frequently (30 second cache).

    Note: coins_summary requires symbol parameter.

    Examples:
        - BTC metrics: action="coins_summary", symbol="BTC"
        - All pairs: action="pairs_summary"
        - Price changes: action="price_changes"
    """
    client = get_client(ctx)

    if action == "coins_summary" and not symbol:
        raise ValueError("Action 'coins_summary' requires symbol parameter (e.g., symbol='BTC')")

    endpoints: dict[ActionMarketData, str | list[str]] = {
        "coins_summary": "/api/futures/coins-markets",
        "pairs_summary": "/api/futures/pairs-markets",
        "price_changes": [
            "/api/futures/price-change-list",
            "/futures/price-change-list",
        ],
    }

    params = {"symbol": symbol} if symbol else None
    data = await request_with_fallback(client, endpoints[action], params)

    return ok(
        action,
        data,
        symbol=symbol,
        total=len(data) if isinstance(data, list) else None,
    )


@mcp.tool(
    name="coinglass_price_history",
    annotations={
        "title": "CoinGlass Price History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_price_history(
    exchange: Annotated[
        str, Field(description="Exchange name (e.g., 'Binance', 'OKX', 'Bybit')")
    ],
    pair: Annotated[
        str, Field(description="Trading pair (e.g., 'BTCUSDT', 'ETHUSDT')")
    ],
    interval: Annotated[
        str,
        Field(description="Candle interval: m1, m5, m15, m30, h1, h4, d1"),
    ],
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of candles to return")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get historical OHLC price data for a specific trading pair.

    Returns candlestick data with timestamp, open, high, low, close, and volume.
    Useful for technical analysis and charting.

    Note: Smaller intervals (m1, m5, m15) require Standard+ plan.

    Examples:
        - Hourly BTC: exchange="Binance", pair="BTCUSDT", interval="h1"
        - Daily ETH: exchange="OKX", pair="ETHUSDT", interval="d1"
    """
    check_interval(ctx, interval)
    client = get_client(ctx)

    data = await client.request(
        "/api/price/ohlc-history",
        {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        },
    )

    return ok(
        "price_history",
        data,
        exchange=exchange,
        pair=pair,
        interval=interval,
        total=len(data) if isinstance(data, list) else None,
    )


# ============================================================================
# OPEN INTEREST TOOLS (2 tools)
# ============================================================================


ActionOIHistory = Literal["pair", "aggregated", "stablecoin", "coin_margin"]


@mcp.tool(
    name="coinglass_oi_history",
    annotations={
        "title": "CoinGlass Open Interest History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_oi_history(
    action: Annotated[
        ActionOIHistory,
        Field(
            description="pair: single pair OI | aggregated: all exchanges combined | stablecoin: USDT-margined only | coin_margin: coin-margined only"
        ),
    ],
    symbol: Annotated[
        str | None,
        Field(description="Coin symbol for aggregated actions (e.g., 'BTC', 'ETH')"),
    ] = None,
    exchange: Annotated[
        str | None,
        Field(description="Exchange for 'pair' action (e.g., 'Binance')"),
    ] = None,
    pair: Annotated[
        str | None,
        Field(description="Trading pair for 'pair' action (e.g., 'BTCUSDT')"),
    ] = None,
    interval: Annotated[
        str, Field(description="Candle interval: h1, h4, d1")
    ] = "h4",
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of candles")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get Open Interest OHLC history.

    Open Interest represents the total number of outstanding derivative contracts.
    Rising OI with rising price = bullish, Rising OI with falling price = bearish.

    Required params by action:
        - pair: exchange + pair
        - aggregated/stablecoin/coin_margin: symbol

    Examples:
        - BTC OI across all exchanges: action="aggregated", symbol="BTC"
        - Binance BTCUSDT OI: action="pair", exchange="Binance", pair="BTCUSDT"
    """
    check_interval(ctx, interval)
    check_params(action, symbol=symbol, exchange=exchange, pair=pair)
    client = get_client(ctx)

    endpoints = {
        "pair": "/api/futures/openInterest/ohlc-history",
        "aggregated": "/api/futures/openInterest/ohlc-aggregated-history",
        "stablecoin": "/api/futures/openInterest/ohlc-aggregated-stablecoin",
        "coin_margin": "/api/futures/openInterest/ohlc-aggregated-coin-margin-history",
    }

    if action == "pair":
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    else:
        params = {"symbol": symbol, "interval": interval, "limit": limit}

    data = await client.request(endpoints[action], params)

    return ok(
        action,
        data,
        symbol=symbol or pair,
        exchange=exchange,
        interval=interval,
        total=len(data) if isinstance(data, list) else None,
    )


ActionOIDist = Literal["by_exchange", "exchange_chart"]


@mcp.tool(
    name="coinglass_oi_distribution",
    annotations={
        "title": "CoinGlass OI Distribution",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_oi_distribution(
    action: Annotated[
        ActionOIDist,
        Field(
            description="by_exchange: current OI breakdown by exchange | exchange_chart: historical OI by exchange"
        ),
    ],
    symbol: Annotated[str, Field(description="Coin symbol (e.g., 'BTC', 'ETH')")],
    range: Annotated[
        str | None,
        Field(description="Time range for exchange_chart: 4h, 12h, 24h, 3d"),
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get Open Interest distribution across exchanges.

    Shows how OI is distributed among different exchanges, useful for
    understanding market concentration and finding arbitrage opportunities.

    Examples:
        - BTC OI by exchange: action="by_exchange", symbol="BTC"
        - Historical distribution: action="exchange_chart", symbol="BTC", range="24h"
    """
    check_params(action, symbol=symbol, range=range)
    client = get_client(ctx)

    if action == "by_exchange":
        data = await client.request(
            "/api/futures/openInterest/exchange-list", {"symbol": symbol}
        )
    else:
        data = await client.request(
            "/api/futures/openInterest/exchange-history-chart",
            {"symbol": symbol, "range": range},
        )

    return ok(action, data, symbol=symbol, range=range)


# ============================================================================
# FUNDING RATE TOOLS (2 tools)
# ============================================================================


ActionFundingHistory = Literal["pair", "oi_weighted", "vol_weighted"]


@mcp.tool(
    name="coinglass_funding_history",
    annotations={
        "title": "CoinGlass Funding History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_funding_history(
    action: Annotated[
        ActionFundingHistory,
        Field(
            description="pair: single pair funding | oi_weighted: OI-weighted average | vol_weighted: volume-weighted average"
        ),
    ],
    symbol: Annotated[
        str | None,
        Field(description="Coin for weighted actions (e.g., 'BTC')"),
    ] = None,
    exchange: Annotated[
        str | None,
        Field(description="Exchange for 'pair' action"),
    ] = None,
    pair: Annotated[
        str | None,
        Field(description="Trading pair for 'pair' action"),
    ] = None,
    interval: Annotated[
        str, Field(description="Interval: h1, h4, h8, d1")
    ] = "h8",
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of records")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get funding rate OHLC history.

    Funding rates are periodic payments between long and short traders.
    Positive rate = longs pay shorts (bullish sentiment).
    Negative rate = shorts pay longs (bearish sentiment).

    Required params by action:
        - pair: exchange + pair
        - oi_weighted/vol_weighted: symbol

    Examples:
        - BTC OI-weighted funding: action="oi_weighted", symbol="BTC"
        - Binance BTCUSDT funding: action="pair", exchange="Binance", pair="BTCUSDT"
    """
    check_interval(ctx, interval)
    check_params(action, symbol=symbol, exchange=exchange, pair=pair)
    client = get_client(ctx)

    endpoints = {
        "pair": "/api/futures/fundingRate/ohlc-history",
        "oi_weighted": "/api/futures/fundingRate/oi-weight-ohlc-history",
        "vol_weighted": "/api/futures/fundingRate/vol-weight-ohlc-history",
    }

    if action == "pair":
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    else:
        params = {"symbol": symbol, "interval": interval, "limit": limit}

    data = await client.request(endpoints[action], params)

    return ok(
        action,
        data,
        symbol=symbol or pair,
        interval=interval,
        total=len(data) if isinstance(data, list) else None,
    )


ActionFundingCurrent = Literal["rates", "accumulated", "arbitrage"]


@mcp.tool(
    name="coinglass_funding_current",
    annotations={
        "title": "CoinGlass Current Funding",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_funding_current(
    action: Annotated[
        ActionFundingCurrent,
        Field(
            description="rates: current funding rates | accumulated: cumulative funding | arbitrage: cross-exchange arbitrage opportunities"
        ),
    ],
    symbol: Annotated[
        str | None, Field(description="Filter by coin (e.g., 'BTC')")
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get current funding rate data across exchanges.

    - rates: Current funding rates by exchange
    - accumulated: Cumulative funding over time
    - arbitrage: Funding rate arbitrage opportunities between exchanges

    Examples:
        - All current rates: action="rates"
        - BTC rates only: action="rates", symbol="BTC"
        - Find arbitrage: action="arbitrage"
    """
    client = get_client(ctx)

    endpoints = {
        "rates": "/api/futures/fundingRate/exchange-list",
        "accumulated": "/api/futures/fundingRate/accumulated-exchange-list",
        "arbitrage": "/api/futures/fundingRate/arbitrage",
    }

    params = {"symbol": symbol} if symbol else None
    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol)


# ============================================================================
# LONG/SHORT RATIO TOOL (1 tool)
# ============================================================================


ActionLS = Literal["global", "top_accounts", "top_positions", "taker_ratio"]


@mcp.tool(
    name="coinglass_long_short",
    annotations={
        "title": "CoinGlass Long/Short Ratio",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_long_short(
    action: Annotated[
        ActionLS,
        Field(
            description="global: global L/S ratio | top_accounts: top traders by account | top_positions: top traders by position | taker_ratio: taker buy/sell ratio"
        ),
    ],
    exchange: Annotated[
        str, Field(description="Exchange (e.g., 'Binance', 'OKX')")
    ],
    pair: Annotated[str, Field(description="Trading pair (e.g., 'BTCUSDT')")],
    interval: Annotated[
        str, Field(description="Interval: m5, m15, m30, h1, h4, d1")
    ] = "h4",
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of records")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get long/short ratio data.

    Long/short ratio shows market sentiment:
    - Ratio > 1: More traders are long (bullish sentiment)
    - Ratio < 1: More traders are short (bearish sentiment)

    Actions:
        - global: Overall account ratio
        - top_accounts: Top traders by number of accounts
        - top_positions: Top traders by position size
        - taker_ratio: Taker buy/sell volume ratio

    Examples:
        - BTC L/S on Binance: exchange="Binance", pair="BTCUSDT", action="global"
    """
    check_interval(ctx, interval)
    client = get_client(ctx)

    endpoints: dict[ActionLS, str | list[str]] = {
        "global": [
            "/api/futures/globalLongShortAccountRatio/history",
            "/api/futures/global-long-short-account-ratio/history",
        ],
        "top_accounts": [
            "/api/futures/topLongShortAccountRatio/history",
            "/api/futures/top-long-short-account-ratio/history",
        ],
        "top_positions": [
            "/api/futures/topLongShortPositionRatio/history",
            "/api/futures/top-long-short-position-ratio/history",
        ],
        "taker_ratio": "/api/futures/taker-buy-sell-volume/exchange-list",
    }

    if action == "taker_ratio":
        params = {"symbol": pair, "range": "24h"}
    else:
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }

    data = await request_with_fallback(client, endpoints[action], params)

    return ok(
        action,
        data,
        exchange=exchange,
        pair=pair,
        interval=interval,
        total=len(data) if isinstance(data, list) else None,
    )


# ============================================================================
# LIQUIDATION TOOLS (3 tools)
# ============================================================================


ActionLiqHistory = Literal["pair", "aggregated", "by_coin", "by_exchange"]


@mcp.tool(
    name="coinglass_liq_history",
    annotations={
        "title": "CoinGlass Liquidation History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_liq_history(
    action: Annotated[
        ActionLiqHistory,
        Field(
            description="pair: single pair liquidations | aggregated: by coin | by_coin: coin summary | by_exchange: exchange summary"
        ),
    ],
    symbol: Annotated[
        str | None, Field(description="Coin for aggregated/by_coin")
    ] = None,
    exchange: Annotated[
        str | None, Field(description="Exchange for pair action")
    ] = None,
    pair: Annotated[
        str | None, Field(description="Trading pair for pair action")
    ] = None,
    interval: Annotated[
        str, Field(description="Interval: m5, h1, h4, h12, d1")
    ] = "h1",
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of records")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get liquidation history data.

    Liquidations occur when a trader's position is forcibly closed due to
    insufficient margin. Large liquidation clusters can indicate support/resistance.

    Examples:
        - BTC liquidations: action="aggregated", symbol="BTC"
        - All coins summary: action="by_coin"
        - By exchange: action="by_exchange"
    """
    check_interval(ctx, interval)
    client = get_client(ctx)

    endpoints = {
        "pair": "/api/futures/liquidation/history",
        "aggregated": "/api/futures/liquidation/aggregated-history",
        "by_coin": "/api/futures/liquidation/coin-list",
        "by_exchange": "/api/futures/liquidation/exchange-list",
    }

    if action == "pair":
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    elif action == "aggregated":
        params = {"symbol": symbol, "interval": interval, "limit": limit}
    else:
        params = {}

    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol or pair)


@mcp.tool(
    name="coinglass_liq_orders",
    annotations={
        "title": "CoinGlass Liquidation Orders",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_liq_orders(
    symbol: Annotated[
        str | None, Field(description="Filter by coin (e.g., 'BTC')")
    ] = None,
    side: Annotated[
        Literal["long", "short"] | None, Field(description="Filter by side")
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=100, description="Number of orders")
    ] = 50,
    ctx: Context = None,
) -> dict:
    """Get real-time liquidation orders stream.

    Returns recent liquidation orders as they happen. Useful for monitoring
    market stress and potential cascade liquidations.

    Note: Requires Standard+ plan.

    Examples:
        - All recent liquidations: (no params)
        - BTC longs only: symbol="BTC", side="long"
    """
    check_plan(ctx, "orders")
    client = get_client(ctx)

    params = {"symbol": symbol, "side": side, "limit": limit}
    data = await client.request("/api/futures/liquidation/order", params)

    return ok("orders", data, symbol=symbol, side=side)


ActionLiqHeatmap = Literal["pair_heatmap", "coin_heatmap", "pair_map", "coin_map"]


@mcp.tool(
    name="coinglass_liq_heatmap",
    annotations={
        "title": "CoinGlass Liquidation Heatmap",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_liq_heatmap(
    action: Annotated[
        ActionLiqHeatmap,
        Field(
            description="pair_heatmap/coin_heatmap: liquidation visualization | pair_map/coin_map: leverage level distribution"
        ),
    ],
    symbol: Annotated[
        str | None, Field(description="Coin for coin_* actions")
    ] = None,
    exchange: Annotated[
        str | None, Field(description="Exchange for pair_* actions")
    ] = None,
    pair: Annotated[
        str | None, Field(description="Trading pair for pair_* actions")
    ] = None,
    range: Annotated[
        str, Field(description="Time range: 3d, 7d, 14d, 30d, 90d, 180d, 1y")
    ] = "7d",
    model: Annotated[
        Literal[1, 2, 3],
        Field(description="Heatmap model: 1=basic, 2=volume-weighted, 3=order-flow"),
    ] = 1,
    ctx: Context = None,
) -> dict:
    """Get liquidation heatmap/map visualization data.

    Heatmaps show where liquidations are concentrated at different price levels.
    Useful for identifying potential support/resistance and cascade zones.

    Note: Requires Professional+ plan.

    Examples:
        - BTC liquidation heatmap: action="coin_heatmap", symbol="BTC", range="7d"
    """
    check_plan(ctx, action)
    if "pair" in action and (not exchange or not pair):
        raise ValueError(
            "Pair heatmap/map actions require exchange + pair "
            "(e.g., exchange='Binance', pair='BTCUSDT')"
        )
    if "coin" in action and not symbol:
        raise ValueError("Coin heatmap/map actions require symbol (e.g., symbol='BTC')")
    client = get_client(ctx)

    endpoints = {
        "pair_heatmap": f"/api/futures/liquidation/heatmap/model{model}",
        "coin_heatmap": f"/api/futures/liquidation/aggregated-heatmap/model{model}",
        "pair_map": "/api/futures/liquidation/map",
        "coin_map": "/api/futures/liquidation/aggregated-map",
    }

    if "pair" in action:
        params = {"exchange": exchange, "symbol": pair, "range": range}
    else:
        params = {"symbol": symbol, "range": range}

    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol or pair, range=range, model=model)


# ============================================================================
# ORDER BOOK TOOLS (2 tools)
# ============================================================================


ActionOB = Literal["pair_depth", "coin_depth", "heatmap"]


@mcp.tool(
    name="coinglass_ob_history",
    annotations={
        "title": "CoinGlass Order Book History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_ob_history(
    action: Annotated[
        ActionOB,
        Field(
            description="pair_depth: pair bid/ask depth | coin_depth: aggregated depth | heatmap: orderbook heatmap"
        ),
    ],
    symbol: Annotated[
        str | None, Field(description="Coin for coin_depth")
    ] = None,
    exchange: Annotated[
        str | None, Field(description="Exchange for pair_depth")
    ] = None,
    pair: Annotated[
        str | None, Field(description="Trading pair for pair_depth")
    ] = None,
    interval: Annotated[
        str, Field(description="Interval: m5, m15, h1, h4")
    ] = "h1",
    range: Annotated[
        str, Field(description="Depth range from mid price: 1, 2, 5 (%)")
    ] = "2",
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of records")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get order book depth history.

    Shows historical bid/ask depth at various price levels. The bid/ask ratio
    can indicate buying or selling pressure.

    Examples:
        - BTC depth on Binance: action="pair_depth", exchange="Binance", pair="BTCUSDT"
        - Aggregated BTC depth: action="coin_depth", symbol="BTC"
    """
    check_interval(ctx, interval)
    client = get_client(ctx)

    endpoints = {
        "pair_depth": "/api/futures/orderbook/ask-bids-history",
        "coin_depth": "/api/futures/orderbook/aggregated-ask-bids-history",
        "heatmap": "/api/futures/orderbook/history",
    }

    if action == "pair_depth":
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "range": range,
            "limit": limit,
        }
    else:
        params = {
            "symbol": symbol,
            "interval": interval,
            "range": range,
            "limit": limit,
        }

    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol or pair, interval=interval, range=range)


ActionLargeOrders = Literal["current", "history"]


@mcp.tool(
    name="coinglass_ob_large_orders",
    annotations={
        "title": "CoinGlass Large Orders",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_ob_large_orders(
    action: Annotated[
        ActionLargeOrders,
        Field(description="current: active large orders | history: historical"),
    ],
    exchange: Annotated[
        str | None, Field(description="Filter by exchange")
    ] = None,
    pair: Annotated[str | None, Field(description="Filter by pair")] = None,
    limit: Annotated[
        int, Field(ge=1, le=100, description="Number of orders")
    ] = 50,
    ctx: Context = None,
) -> dict:
    """Get large limit orders (whale walls).

    Detects significant limit orders that may act as support/resistance.
    Thresholds: BTC >= $1M, ETH >= $500K, others >= $50K.

    Examples:
        - Current whale walls: action="current"
        - Historical large orders: action="history"
    """
    client = get_client(ctx)

    endpoint = (
        "/api/futures/orderbook/large-limit-order"
        if action == "current"
        else "/api/futures/orderbook/large-limit-order-history"
    )

    params = {"exchange": exchange, "symbol": pair, "limit": limit}
    data = await client.request(endpoint, params)

    return ok(action, data, exchange=exchange, pair=pair)


# ============================================================================
# WHALE TOOLS (2 tools)
# ============================================================================


ActionWhale = Literal["alerts", "positions", "all_positions"]


@mcp.tool(
    name="coinglass_whale_positions",
    annotations={
        "title": "CoinGlass Whale Positions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_whale_positions(
    action: Annotated[
        ActionWhale,
        Field(
            description="alerts: real-time whale alerts | positions: positions >$1M | all_positions: all Hyperliquid positions"
        ),
    ],
    symbol: Annotated[
        str | None, Field(description="Filter by coin")
    ] = None,
    user: Annotated[
        str | None, Field(description="Filter by wallet address")
    ] = None,
    page: Annotated[int, Field(ge=1, description="Page number")] = 1,
    ctx: Context = None,
) -> dict:
    """Track whale activity on Hyperliquid.

    Monitor large traders' positions and activity. Useful for following
    smart money and identifying potential market moves.

    Note: Requires Startup+ plan.

    Examples:
        - Recent whale alerts: action="alerts"
        - Large BTC positions: action="positions", symbol="BTC"
        - Track specific wallet: action="all_positions", user="0x..."
    """
    check_plan(ctx, action)
    client = get_client(ctx)

    endpoints = {
        "alerts": "/api/hyperliquid/whale-alert",
        "positions": "/api/hyperliquid/whale-position",
        "all_positions": "/api/hyperliquid/position",
    }

    params = {"symbol": symbol, "user": user, "page": page}
    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol, page=page)


@mcp.tool(
    name="coinglass_bitfinex_longs_shorts",
    annotations={
        "title": "CoinGlass Bitfinex Margin",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_bitfinex_longs_shorts(
    symbol: Annotated[
        str, Field(description="Coin symbol (e.g., 'BTC', 'ETH')")
    ] = "BTC",
    ctx: Context = None,
) -> dict:
    """Get Bitfinex margin long/short data.

    Shows margin positions on Bitfinex exchange.
    Useful for gauging sentiment among margin traders.

    Examples:
        - BTC margin positions: symbol="BTC"
        - ETH margin positions: symbol="ETH"
    """
    client = get_client(ctx)
    data = await client.request("/api/bitfinex-margin-long-short", {"symbol": symbol})
    return ok("bitfinex_margin", data, symbol=symbol)


# ============================================================================
# TAKER TOOL (1 tool)
# ============================================================================


ActionTaker = Literal["pair_history", "coin_history", "by_exchange"]


@mcp.tool(
    name="coinglass_taker",
    annotations={
        "title": "CoinGlass Taker Volume",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_taker(
    action: Annotated[
        ActionTaker,
        Field(
            description="pair_history: single pair | coin_history: aggregated | by_exchange: ratio by exchange"
        ),
    ],
    symbol: Annotated[
        str | None, Field(description="Coin for coin_history/by_exchange")
    ] = None,
    exchange: Annotated[
        str | None, Field(description="Exchange for pair_history")
    ] = None,
    pair: Annotated[
        str | None, Field(description="Trading pair for pair_history")
    ] = None,
    interval: Annotated[
        str, Field(description="Interval: m5, m15, h1, h4, d1")
    ] = "h1",
    range: Annotated[
        str | None,
        Field(
            description="For by_exchange only: 4h, 12h, 24h. "
            "Defaults to 24h if omitted."
        ),
    ] = None,
    exchange_list: Annotated[
        str | None,
        Field(
            description="Comma-separated exchanges for coin_history aggregation "
            "(e.g., 'Binance,OKX,Bybit')"
        ),
    ] = None,
    market: Annotated[
        Literal["futures", "spot"], Field(description="Market type")
    ] = "futures",
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of records")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get taker buy/sell volume data.

    Taker volume shows market order activity:
    - Buy ratio > 0.5: More aggressive buying (bullish)
    - Buy ratio < 0.5: More aggressive selling (bearish)

    Examples:
        - BTC taker volume: action="coin_history", symbol="BTC"
        - By exchange: action="by_exchange", symbol="BTC"
    """
    check_interval(ctx, interval)
    client = get_client(ctx)

    if market == "futures":
        endpoints: dict[ActionTaker, str | list[str]] = {
            "pair_history": "/api/futures/taker-buy-sell-volume/history",
            "coin_history": [
                "/api/futures/taker-buy-sell-volume/aggregated-history",
                "/api/futures/aggregated-taker-buy-sell-volume/history",
            ],
            "by_exchange": "/api/futures/taker-buy-sell-volume/exchange-list",
        }
    else:
        endpoints = {
            "pair_history": "/api/spot/taker-buy-sell-volume/history",
            "coin_history": [
                "/api/spot/taker-buy-sell-volume/aggregated-history",
                "/api/spot/aggregated-taker-buy-sell-volume/history",
            ],
            "by_exchange": "/api/spot/taker-buy-sell-volume/exchange-list",
        }

    if action == "pair_history":
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    elif action == "coin_history":
        default_exchange_list = (
            "Binance,OKX,Bybit,dYdX,Bitget,Huobi,Gate,CoinEx,Kraken,BingX"
            if market == "futures"
            else "Binance,OKX,Coinbase,Bybit,Kraken,Huobi,Gate,Bitfinex,KuCoin"
        )
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "exchange_list": exchange_list or default_exchange_list,
        }
    else:
        if market != "futures":
            raise ValueError("Action 'by_exchange' is only available for futures market")
        params = {"symbol": symbol, "range": range or "24h"}

    data = await request_with_fallback(client, endpoints[action], params)

    return ok(
        action,
        data,
        symbol=symbol or pair,
        market=market,
        interval=interval,
        range=range or ("24h" if action == "by_exchange" else None),
    )


# ============================================================================
# SPOT TOOL (1 tool)
# ============================================================================


ActionSpot = Literal[
    "coins",
    "pairs",
    "coins_markets",
    "pairs_markets",
    "price_history",
    "taker_history",
    "taker_aggregated_history",
]


@mcp.tool(
    name="coinglass_spot",
    annotations={
        "title": "CoinGlass Spot Market",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_spot(
    action: Annotated[
        ActionSpot,
        Field(
            description="coins: supported coins | pairs: exchange pairs | coins_markets: coin data | pairs_markets: pair data | price_history: OHLC | taker_history: pair taker volume | taker_aggregated_history: aggregated taker volume"
        ),
    ],
    symbol: Annotated[str | None, Field(description="Coin filter")] = None,
    exchange: Annotated[
        str | None, Field(description="Exchange (required for price_history/taker_history)")
    ] = None,
    pair: Annotated[
        str | None, Field(description="Pair (required for price_history/taker_history)")
    ] = None,
    interval: Annotated[
        str | None, Field(description="Interval for price/taker history: h1, h4, d1")
    ] = None,
    exchange_list: Annotated[
        str | None,
        Field(
            description="Comma-separated exchanges for taker_aggregated_history "
            "(e.g., 'Binance,OKX,Coinbase')"
        ),
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=4500, description="Number of records")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get spot market data.

    Access spot market information including supported coins, trading pairs,
    market summaries, and historical prices.

    Examples:
        - List spot coins: action="coins"
        - Spot market data: action="coins_markets"
        - Price history: action="price_history", exchange="Binance", pair="BTCUSDT", interval="h1"
    """
    client = get_client(ctx)

    endpoints = {
        "coins": "/api/spot/supported-coins",
        "pairs": "/api/spot/supported-exchange-pairs",
        "coins_markets": "/api/spot/coins-markets",
        "pairs_markets": "/api/spot/pairs-markets",
        "price_history": "/api/price/ohlc-history",
        "taker_history": "/api/spot/taker-buy-sell-volume/history",
        "taker_aggregated_history": [
            "/api/spot/taker-buy-sell-volume/aggregated-history",
            "/api/spot/aggregated-taker-buy-sell-volume/history",
        ],
    }

    if action == "price_history":
        if interval:
            check_interval(ctx, interval)
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    elif action == "taker_history":
        taker_interval = interval or "h1"
        check_interval(ctx, taker_interval)
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": taker_interval,
            "limit": limit,
        }
    elif action == "taker_aggregated_history":
        taker_interval = interval or "h1"
        check_interval(ctx, taker_interval)
        params = {
            "symbol": symbol,
            "interval": taker_interval,
            "limit": limit,
            "exchange_list": (
                exchange_list
                or "Binance,OKX,Coinbase,Bybit,Kraken,Huobi,Gate,Bitfinex,KuCoin"
            ),
        }
    else:
        params = {}

    data = await request_with_fallback(client, endpoints[action], params)

    return ok(
        action,
        data,
        symbol=symbol or pair,
        exchange=exchange,
        interval=interval or ("h1" if "taker" in action else None),
    )


# ============================================================================
# OPTIONS TOOL (1 tool)
# ============================================================================


ActionOptions = Literal["max_pain", "info", "oi_history", "volume_history"]


@mcp.tool(
    name="coinglass_options",
    annotations={
        "title": "CoinGlass Options",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_options(
    action: Annotated[
        ActionOptions,
        Field(
            description="max_pain: max pain price | info: OI/volume summary | oi_history: OI over time | volume_history: volume over time"
        ),
    ],
    symbol: Annotated[
        Literal["BTC", "ETH"], Field(description="BTC or ETH only")
    ],
    range: Annotated[
        str | None, Field(description="Time range: 7d, 30d, 90d")
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get options market data from Deribit, OKX, Binance, Bybit.

    Options data helps understand market expectations:
    - Max pain: Price where most options expire worthless
    - Put/Call ratio: Sentiment indicator

    Examples:
        - BTC max pain: action="max_pain", symbol="BTC"
        - Options OI: action="info", symbol="ETH"
    """
    client = get_client(ctx)

    endpoints = {
        "max_pain": "/api/option/max-pain",
        "info": "/api/option/info",
        "oi_history": "/api/option/exchange-oi-history",
        "volume_history": "/api/option/exchange-vol-history",
    }

    params = {"symbol": symbol}
    if range:
        params["range"] = range

    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol, range=range)


# ============================================================================
# ON-CHAIN TOOL (1 tool)
# ============================================================================


ActionOnChain = Literal["assets", "balance_list", "balance_chart", "transfers"]


@mcp.tool(
    name="coinglass_onchain",
    annotations={
        "title": "CoinGlass On-Chain",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_onchain(
    action: Annotated[
        ActionOnChain,
        Field(
            description="assets: exchange holdings | balance_list: balances by asset | balance_chart: historical | transfers: ERC-20 transactions"
        ),
    ],
    exchange: Annotated[str | None, Field(description="Exchange filter")] = None,
    asset: Annotated[
        str | None, Field(description="Asset: BTC, ETH, USDT")
    ] = None,
    range: Annotated[
        str | None, Field(description="Time range: 7d, 30d, 90d")
    ] = None,
    transfer_type: Annotated[
        Literal["inflow", "outflow", "internal"] | None,
        Field(description="Filter transfers by type"),
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=100, description="Number of records")
    ] = 50,
    ctx: Context = None,
) -> dict:
    """Get on-chain exchange data.

    Track exchange holdings and flows:
    - Increasing exchange balance: Potential selling pressure
    - Decreasing exchange balance: Accumulation (bullish)

    Examples:
        - All exchange holdings: action="assets"
        - BTC balances: action="balance_list", asset="BTC"
        - Balance history: action="balance_chart", asset="BTC", exchange="Binance"
    """
    client = get_client(ctx)

    endpoints = {
        "assets": "/api/exchange/assets",
        "balance_list": "/api/exchange/balance/list",
        "balance_chart": "/api/exchange/balance/chart",
        "transfers": "/api/exchange/chain/tx/list",
    }

    params = {
        "exchange": exchange,
        "asset": asset,
        "range": range,
        "type": transfer_type,
        "limit": limit,
    }

    data = await client.request(endpoints[action], params)

    return ok(action, data, exchange=exchange, asset=asset)


# ============================================================================
# ETF TOOLS (2 tools)
# ============================================================================


ActionETF = Literal[
    "list",
    "flows",
    "history",
    "net_assets",
    "premium",
    "detail",
    "price",
    "bitcoin_list",
    "bitcoin_flows",
    "bitcoin_history",
    "bitcoin_net_assets",
    "bitcoin_premium_discount",
    "bitcoin_detail",
    "bitcoin_price",
]


@mcp.tool(
    name="coinglass_etf",
    annotations={
        "title": "CoinGlass ETF",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_etf(
    action: Annotated[
        ActionETF,
        Field(
            description="list/flows/history/net_assets/premium/detail/price: generic by asset parameter | bitcoin_*: explicit Bitcoin ETF endpoints"
        ),
    ],
    asset: Annotated[
        Literal["bitcoin", "ethereum"], Field(description="BTC or ETH ETFs")
    ] = "bitcoin",
    ticker: Annotated[
        str | None, Field(description="ETF ticker: IBIT, GBTC, ETHE")
    ] = None,
    region: Annotated[
        Literal["us", "hk"] | None, Field(description="US or Hong Kong")
    ] = "us",
    interval: Annotated[
        str | None, Field(description="For price: h1, d1")
    ] = None,
    range: Annotated[
        str | None, Field(description="For history/price: 7d, 30d, 90d")
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=500, description="Number of records")
    ] = 100,
    ctx: Context = None,
) -> dict:
    """Get crypto ETF data (Bitcoin & Ethereum).

    Track institutional flows through ETFs:
    - Positive flows: Institutional buying (bullish)
    - Negative flows: Institutional selling (bearish)

    Examples:
        - List Bitcoin ETFs: action="list", asset="bitcoin"
        - Daily flows: action="flows", asset="bitcoin"
        - IBIT premium: action="premium", ticker="IBIT"
    """
    client = get_client(ctx)

    if action in {"premium", "detail", "price", "bitcoin_premium_discount", "bitcoin_detail", "bitcoin_price"} and not ticker:
        raise ValueError(
            f"Action '{action}' requires ticker parameter (e.g., ticker='IBIT')"
        )

    base = f"/api/etf/{asset}"
    endpoints: dict[ActionETF, str | list[str]] = {
        "list": f"{base}/list",
        "flows": [f"{base}/flows", f"{base}/flow-history"],
        "history": f"{base}/history",
        "net_assets": [f"{base}/net-assets", f"{base}/net-assets/history"],
        "premium": [f"{base}/premium-discount", f"{base}/premium-discount/history"],
        "detail": f"{base}/detail",
        "price": [f"{base}/price", f"{base}/price/history"],
        "bitcoin_list": "/api/etf/bitcoin/list",
        "bitcoin_flows": "/api/etf/bitcoin/flows",
        "bitcoin_history": "/api/etf/bitcoin/history",
        "bitcoin_net_assets": "/api/etf/bitcoin/net-assets",
        "bitcoin_premium_discount": "/api/etf/bitcoin/premium-discount",
        "bitcoin_detail": "/api/etf/bitcoin/detail",
        "bitcoin_price": "/api/etf/bitcoin/price",
    }

    asset_value = "bitcoin" if action.startswith("bitcoin_") else asset
    params = {
        "ticker": ticker,
        "region": region,
        "range": range or ("7d" if action in {"price", "bitcoin_price"} else None),
        "interval": interval,
        "limit": limit,
    }

    data = await request_with_fallback(client, endpoints[action], params)

    return ok(action, data, asset=asset_value, ticker=ticker, range=range)


ActionGrayscale = Literal["holdings", "premium"]


@mcp.tool(
    name="coinglass_grayscale",
    annotations={
        "title": "CoinGlass Grayscale",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_grayscale(
    action: Annotated[
        ActionGrayscale,
        Field(description="holdings: current holdings | premium: premium history"),
    ],
    fund: Annotated[
        str | None, Field(description="Fund: GBTC, ETHE, etc.")
    ] = None,
    range: Annotated[
        str | None, Field(description="Time range: 30d, 90d, 1y")
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get Grayscale fund data.

    Grayscale premium/discount indicates institutional sentiment:
    - Premium: Strong demand (bullish)
    - Discount: Weak demand or selling pressure

    Examples:
        - All Grayscale holdings: action="holdings"
        - GBTC premium history: action="premium", fund="GBTC", range="90d"
    """
    client = get_client(ctx)

    endpoints: dict[ActionGrayscale, str | list[str]] = {
        "holdings": ["/api/grayscale/holdings", "/api/grayscale/holdings-list"],
        "premium": ["/api/grayscale/premium", "/api/grayscale/premium-history"],
    }

    params = {"fund": fund, "range": range}
    data = await request_with_fallback(client, endpoints[action], params)

    return ok(action, data, fund=fund, range=range)


# ============================================================================
# INDICATORS TOOL (1 tool)
# ============================================================================


ActionIndicators = Literal[
    "rsi",
    "basis",
    "coinbase_premium",
    "fear_greed",
    "ahr999",
    "puell",
    "stock_flow",
    "pi_cycle",
    "rainbow",
    "bubble",
    "ma_2year",
    "ma_200week",
    "profitable_days",
    "stablecoin_mcap",
    "bull_peak",
    "borrow_rate",
    "whale_index",
]


@mcp.tool(
    name="coinglass_indicators",
    annotations={
        "title": "CoinGlass Indicators",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_indicators(
    action: Annotated[
        ActionIndicators,
        Field(
            description="Market indicator: rsi, basis, coinbase_premium, fear_greed, ahr999, puell, stock_flow, pi_cycle, rainbow, bubble, ma_2year, ma_200week, profitable_days, stablecoin_mcap, bull_peak, borrow_rate, whale_index"
        ),
    ],
    symbol: Annotated[
        str | None, Field(description="Coin for rsi/basis/borrow_rate")
    ] = None,
    exchange: Annotated[
        str | None, Field(description="Exchange for borrow_rate")
    ] = None,
    interval: Annotated[
        str | None, Field(description="Interval for basis: h1, h4, d1")
    ] = None,
    range: Annotated[
        str | None, Field(description="Time range")
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=1000, description="Number of records")
    ] = 500,
    ctx: Context = None,
) -> dict:
    """Get market indicators and on-chain metrics.

    Indicators help identify market cycles:
    - fear_greed: 0-100 (extreme fear to extreme greed)
    - rainbow: Price band indicator for Bitcoin
    - pi_cycle: Bitcoin cycle top indicator

    Most indicators are BTC-only. rsi returns all coins.
    borrow_rate requires symbol + exchange.

    Examples:
        - Fear & Greed: action="fear_greed"
        - RSI all coins: action="rsi"
        - BTC rainbow: action="rainbow"
    """
    client = get_client(ctx)

    endpoints: dict[ActionIndicators, str | list[str]] = {
        "rsi": ["/api/indicator/rsi", "/api/futures/rsi/list"],
        "basis": ["/api/indicator/basis", "/api/futures/basis/history"],
        "coinbase_premium": [
            "/api/indicator/coinbase-premium",
            "/api/coinbase-premium-index",
        ],
        "fear_greed": "/api/index/fear-greed-history",
        "ahr999": "/api/index/ahr999",
        "puell": "/api/index/puell-multiple",
        "stock_flow": ["/api/index/stock-to-flow", "/api/index/stock-flow"],
        "pi_cycle": ["/api/index/pi-cycle-top", "/api/index/pi-cycle-indicator"],
        "rainbow": ["/api/index/rainbow-chart", "/api/index/bitcoin/rainbow-chart"],
        "bubble": ["/api/index/bitcoin-bubble-index", "/api/index/bitcoin/bubble-index"],
        "ma_2year": ["/api/index/two-year-ma-multiplier", "/api/index/2-year-ma-multiplier"],
        "ma_200week": [
            "/api/index/two-hundred-week-ma-heatmap",
            "/api/index/200-week-moving-average-heatmap",
        ],
        "profitable_days": [
            "/api/index/bitcoin-profitable-days",
            "/api/index/bitcoin/profitable-days",
        ],
        "stablecoin_mcap": [
            "/api/index/stablecoin-market-cap",
            "/api/index/stableCoin-marketCap-history",
        ],
        "bull_peak": ["/api/index/bull-market-peak-signals", "/api/bull-market-peak-indicator"],
        "borrow_rate": ["/api/indicator/borrow-rate", "/api/borrow-interest-rate/history"],
        "whale_index": "/api/index/whale-index",
    }

    params = {
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "range": range,
        "limit": limit,
    }

    data = await request_with_fallback(client, endpoints[action], params)

    return ok(action, data, symbol=symbol)


# ============================================================================
# META TOOLS (2 tools)
# ============================================================================


@mcp.tool(
    name="coinglass_search",
    annotations={
        "title": "CoinGlass Search",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def coinglass_search(
    query: Annotated[
        str,
        Field(description="Search query (e.g., 'liquidation BTC', 'funding arbitrage')"),
    ],
    ctx: Context = None,
) -> dict:
    """Search available CoinGlass operations.

    Use this to discover which tool and action to use for your task.
    Returns matching tools with their available actions.

    Examples:
        - Find liquidation tools: query="liquidation"
        - Find funding data: query="funding rate"
        - Find whale tracking: query="whale"
    """
    tools_info = {
        "coinglass_market_info": {
            "actions": ["coins", "pairs", "exchanges"],
            "keywords": ["market", "coins", "exchanges", "pairs", "supported"],
        },
        "coinglass_market_data": {
            "actions": ["coins_summary", "pairs_summary", "price_changes"],
            "keywords": ["market", "price", "summary", "overview"],
        },
        "coinglass_price_history": {
            "actions": [],
            "keywords": ["price", "ohlc", "history", "candles", "chart"],
        },
        "coinglass_oi_history": {
            "actions": ["pair", "aggregated", "stablecoin", "coin_margin"],
            "keywords": ["open interest", "oi", "positions", "history"],
        },
        "coinglass_oi_distribution": {
            "actions": ["by_exchange", "exchange_chart"],
            "keywords": ["open interest", "oi", "distribution", "exchange"],
        },
        "coinglass_funding_history": {
            "actions": ["pair", "oi_weighted", "vol_weighted"],
            "keywords": ["funding", "rate", "history"],
        },
        "coinglass_funding_current": {
            "actions": ["rates", "accumulated", "arbitrage"],
            "keywords": ["funding", "rate", "arbitrage", "current"],
        },
        "coinglass_long_short": {
            "actions": ["global", "top_accounts", "top_positions", "taker_ratio"],
            "keywords": ["long", "short", "ratio", "sentiment"],
        },
        "coinglass_liq_history": {
            "actions": ["pair", "aggregated", "by_coin", "by_exchange"],
            "keywords": ["liquidation", "liq", "history"],
        },
        "coinglass_liq_orders": {
            "actions": [],
            "keywords": ["liquidation", "orders", "real-time", "stream"],
        },
        "coinglass_liq_heatmap": {
            "actions": ["pair_heatmap", "coin_heatmap", "pair_map", "coin_map"],
            "keywords": ["liquidation", "heatmap", "map", "leverage"],
        },
        "coinglass_ob_history": {
            "actions": ["pair_depth", "coin_depth", "heatmap"],
            "keywords": ["orderbook", "depth", "bid", "ask"],
        },
        "coinglass_ob_large_orders": {
            "actions": ["current", "history"],
            "keywords": ["orderbook", "whale", "large", "orders", "walls"],
        },
        "coinglass_whale_positions": {
            "actions": ["alerts", "positions", "all_positions"],
            "keywords": ["whale", "positions", "hyperliquid", "large traders"],
        },
        "coinglass_bitfinex_longs_shorts": {
            "actions": [],
            "keywords": ["bitfinex", "margin", "longs", "shorts"],
        },
        "coinglass_taker": {
            "actions": ["pair_history", "coin_history", "by_exchange"],
            "keywords": ["taker", "buy", "sell", "volume", "aggressor"],
        },
        "coinglass_spot": {
            "actions": [
                "coins",
                "pairs",
                "coins_markets",
                "pairs_markets",
                "price_history",
                "taker_history",
                "taker_aggregated_history",
            ],
            "keywords": ["spot", "market"],
        },
        "coinglass_options": {
            "actions": ["max_pain", "info", "oi_history", "volume_history"],
            "keywords": ["options", "max pain", "calls", "puts", "deribit"],
        },
        "coinglass_onchain": {
            "actions": ["assets", "balance_list", "balance_chart", "transfers"],
            "keywords": ["onchain", "on-chain", "balance", "exchange", "flow", "transfer"],
        },
        "coinglass_etf": {
            "actions": [
                "list",
                "flows",
                "history",
                "net_assets",
                "premium",
                "detail",
                "price",
                "bitcoin_list",
                "bitcoin_flows",
                "bitcoin_history",
                "bitcoin_net_assets",
                "bitcoin_premium_discount",
                "bitcoin_detail",
                "bitcoin_price",
            ],
            "keywords": ["etf", "bitcoin", "ethereum", "flows", "institutional"],
        },
        "coinglass_grayscale": {
            "actions": ["holdings", "premium"],
            "keywords": ["grayscale", "gbtc", "ethe"],
        },
        "coinglass_indicators": {
            "actions": [
                "rsi", "basis", "coinbase_premium", "fear_greed", "ahr999",
                "puell", "stock_flow", "pi_cycle", "rainbow", "bubble",
                "ma_2year", "ma_200week", "profitable_days", "stablecoin_mcap",
                "bull_peak", "borrow_rate", "whale_index",
            ],
            "keywords": [
                "indicator", "rsi", "fear", "greed", "rainbow",
                "cycle", "sentiment", "metric", "coinbase", "premium", "whale",
            ],
        },
    }

    query_lower = query.lower()
    matches = []

    for tool, info in tools_info.items():
        score = sum(1 for kw in info["keywords"] if kw in query_lower)
        if score > 0:
            matches.append({
                "tool": tool,
                "actions": info["actions"],
                "relevance": score,
            })

    matches.sort(key=lambda x: x["relevance"], reverse=True)

    return ok(
        "search",
        {"query": query, "matches": matches[:5]},
        total_matches=len(matches),
    )


@mcp.tool(
    name="coinglass_config",
    annotations={
        "title": "CoinGlass Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def coinglass_config(
    action: Annotated[
        Literal["exchanges", "intervals", "rate_limits", "plan_features"],
        Field(
            description="exchanges: list exchanges | intervals: available intervals | rate_limits: current usage | plan_features: plan capabilities"
        ),
    ],
    ctx: Context = None,
) -> dict:
    """Get CoinGlass configuration and metadata.

    Useful for understanding available options and current limits.

    Examples:
        - List exchanges: action="exchanges"
        - Check intervals: action="intervals"
        - See plan features: action="plan_features"
    """
    plan = get_plan(ctx)

    if action == "exchanges":
        data = {
            "futures": [
                "Binance", "OKX", "Bybit", "dYdX", "Bitget",
                "Huobi", "Gate", "CoinEx", "Kraken", "BingX",
            ],
            "spot": [
                "Binance", "OKX", "Coinbase", "Bybit", "Kraken",
                "Huobi", "Gate", "Bitfinex", "KuCoin",
            ],
            "options": ["Deribit", "OKX", "Binance", "Bybit"],
        }
    elif action == "intervals":
        all_intervals = list(PLAN_INTERVALS.get("enterprise", set()))
        your_intervals = list(PLAN_INTERVALS.get(plan, set()))
        data = {
            "all": sorted(all_intervals),
            "your_plan": sorted(your_intervals),
            "plan": plan,
        }
    elif action == "rate_limits":
        limits = {
            "hobbyist": 30,
            "startup": 80,
            "standard": 300,
            "professional": 600,
            "enterprise": 1200,
        }
        data = {
            "plan": plan,
            "requests_per_minute": limits.get(plan, 30),
            "note": "Check response headers for current usage",
        }
    else:  # plan_features
        data = {
            "plan": plan,
            "features": list(PLAN_FEATURES.get(plan, set())),
            "intervals": list(PLAN_INTERVALS.get(plan, set())),
        }

    return ok(action, data, plan=plan)


# ============================================================================
# ENTRY POINT
# ============================================================================


def main():
    """Run the CoinGlass MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
