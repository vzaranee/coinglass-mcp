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
from typing import Annotated, Any, Literal, Optional

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


_COMMON_QUOTES = (
    "USDT",
    "USDC",
    "FDUSD",
    "BUSD",
    "DAI",
    "USD",
    "BTC",
    "ETH",
    "EUR",
    "JPY",
    "GBP",
    "TRY",
    "BRL",
    "AUD",
)
_CONTRACT_SUFFIXES = ("-SWAP", "_SWAP", "-PERP", "_PERP")
_SUPPORTED_PAIR_INDEX_CACHE: dict[str, dict[str, str]] | None = None


def _normalize_exchange_key(exchange: str) -> str:
    """Normalize exchange names for case/punctuation-insensitive lookup."""
    return "".join(ch for ch in exchange.strip().lower() if ch.isalnum())


def _extract_base_quote(symbol: str) -> tuple[str, str] | None:
    """Extract base/quote from common futures instrument formats."""
    cleaned = symbol.strip().upper().replace("/", "-")
    if not cleaned:
        return None

    for suffix in _CONTRACT_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break

    for delimiter in ("-", "_"):
        if delimiter not in cleaned:
            continue
        parts = [part for part in cleaned.split(delimiter) if part]
        if len(parts) >= 3 and parts[2] in {"SWAP", "PERP"}:
            return parts[0], parts[1]
        if len(parts) == 2 and parts[1] in {"SWAP", "PERP"}:
            return _extract_base_quote(parts[0])
        if len(parts) >= 2:
            return parts[0], parts[1]

    for quote in _COMMON_QUOTES:
        if cleaned.endswith(quote) and len(cleaned) > len(quote):
            return cleaned[: -len(quote)], quote

    return None


def _instrument_candidates(exchange: str | None, pair: str) -> list[str]:
    """Build likely instrument_id candidates, prioritizing exchange conventions."""
    raw = pair.strip()
    if not raw:
        return []

    exchange_key = _normalize_exchange_key(exchange or "")
    upper_raw = raw.upper()
    candidates: list[str] = []
    base_quote = _extract_base_quote(upper_raw)
    if base_quote:
        base, quote = base_quote
        compact = f"{base}{quote}"
        dashed = f"{base}-{quote}"
        underscored = f"{base}_{quote}"
        okx_swap = f"{base}-{quote}-SWAP"
        if exchange_key == "okx":
            candidates.extend([okx_swap, dashed, compact, underscored])
        elif exchange_key == "gate":
            candidates.extend([underscored, compact, dashed, okx_swap])
        else:
            candidates.extend([compact, dashed, underscored, okx_swap])

    candidates.extend([raw, upper_raw])

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.upper()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


async def _get_supported_pair_index(client: CoinGlassClient) -> dict[str, dict[str, str]]:
    """Load exchange->instrument_id index from supported pairs endpoint."""
    global _SUPPORTED_PAIR_INDEX_CACHE
    if _SUPPORTED_PAIR_INDEX_CACHE is not None:
        return _SUPPORTED_PAIR_INDEX_CACHE

    data = await client.request("/api/futures/supported-exchange-pairs")
    index: dict[str, dict[str, str]] = {}
    if isinstance(data, dict):
        for exchange_name, instruments in data.items():
            if not isinstance(exchange_name, str):
                continue
            key = _normalize_exchange_key(exchange_name)
            instrument_map: dict[str, str] = {}
            if isinstance(instruments, list):
                for instrument in instruments:
                    if not isinstance(instrument, dict):
                        continue
                    instrument_id = instrument.get("instrument_id") or instrument.get(
                        "instrumentId"
                    )
                    if isinstance(instrument_id, str) and instrument_id:
                        instrument_map[instrument_id.upper()] = instrument_id
            index[key] = instrument_map
    _SUPPORTED_PAIR_INDEX_CACHE = index
    return index


async def resolve_instrument_id(
    client: CoinGlassClient, exchange: str | None, pair: str | None
) -> str | None:
    """Map a generic pair (e.g. ETHUSDT) to exchange-specific instrument_id."""
    if not pair:
        return pair

    candidates = _instrument_candidates(exchange, pair)
    if not exchange or not candidates:
        return pair

    exchange_key = _normalize_exchange_key(exchange)
    try:
        index = await _get_supported_pair_index(client)
    except Exception:
        # Fallback to best-effort formatting if supported-pairs lookup fails.
        return candidates[0]

    instruments = index.get(exchange_key)
    if not instruments:
        return candidates[0]

    for candidate in candidates:
        match = instruments.get(candidate.upper())
        if match:
            return match

    return candidates[0]


# Canonical OpenAPI v4 paths for historically brittle endpoints.
ENDPOINT_PRICE_OHLC_HISTORY = "/api/futures/price/history"
ENDPOINT_OI_AGGREGATED_OHLC_HISTORY = "/api/futures/open-interest/aggregated-history"
ENDPOINT_FUNDING_ACCUMULATED_HISTORY = (
    "/api/futures/funding-rate/accumulated-exchange-list"
)
ENDPOINT_FUNDING_OI_WEIGHTED_OHLC_HISTORY = (
    "/api/futures/funding-rate/oi-weight-history"
)
ENDPOINT_LIQ_AGGREGATED_HISTORY = "/api/futures/liquidation/aggregated-history"
ENDPOINT_ORDERBOOK_AGGREGATED_ASK_BIDS_HISTORY = (
    "/api/futures/orderbook/aggregated-ask-bids-history"
)
ENDPOINT_BITFINEX_LONG_SHORT_HISTORY = "/api/bitfinex-margin-long-short"
ENDPOINT_ONCHAIN_BALANCE_LIST = "/api/exchange/balance/list"
ENDPOINT_ONCHAIN_WHALE_TRANSFER = "/api/chain/v2/whale-transfer"
ENDPOINT_HYPERLIQUID_WHALE_ALERTS = "/api/hyperliquid/whale-alert"


# ============================================================================
# ARTICLE & CALENDAR TOOLS (2 tools)
# ============================================================================


ActionArticle = Literal["list"]


@mcp.tool(
    name="coinglass_article",
    annotations={
        "title": "CoinGlass Articles",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_article(
    action: Annotated[
        ActionArticle,
        Field(description="list: news/article list"),
    ] = "list",
    language: Annotated[
        str | None, Field(description="Language code (e.g., en)")
    ] = None,
    page: Annotated[
        int | None, Field(ge=1, description="Page number")
    ] = None,
    per_page: Annotated[
        int | None, Field(ge=1, le=100, description="Items per page")
    ] = None,
    start_time: Annotated[
        int | None, Field(description="Start timestamp in milliseconds")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp in milliseconds")
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get CoinGlass article feed."""
    client = get_client(ctx)
    data = await client.request(
        "/api/article/list",
        {
            "language": language,
            "page": page,
            "per_page": per_page,
            "start_time": start_time,
            "end_time": end_time,
        },
    )
    return ok(action, data, page=page, per_page=per_page, language=language)


ActionCalendar = Literal["central_bank_activities", "economic_data", "financial_events"]


@mcp.tool(
    name="coinglass_calendar",
    annotations={
        "title": "CoinGlass Calendar",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def coinglass_calendar(
    action: Annotated[
        ActionCalendar,
        Field(
            description="central_bank_activities: central bank calendar | economic_data: macro releases | financial_events: market events"
        ),
    ],
    start_time: Annotated[
        int | None, Field(description="Start timestamp in milliseconds")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp in milliseconds")
    ] = None,
    language: Annotated[
        str | None, Field(description="Language code for economic_data (e.g., en)")
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get CoinGlass calendar and macro-event data."""
    client = get_client(ctx)
    endpoints: dict[ActionCalendar, str] = {
        "central_bank_activities": "/api/calendar/central-bank-activities",
        "economic_data": "/api/calendar/economic-data",
        "financial_events": "/api/calendar/financial-events",
    }
    params = {
        "start_time": start_time,
        "end_time": end_time,
        "language": language if action == "economic_data" else None,
    }
    data = await client.request(endpoints[action], params)
    return ok(action, data, start_time=start_time, end_time=end_time, language=language)


# ============================================================================
# MARKET TOOLS (3 tools)
# ============================================================================


ActionMarketInfo = Literal[
    "coins",
    "pairs",
    "exchanges",
    "supported_exchanges",
    "delisted_pairs",
]


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
            description="coins: list supported coins | pairs: exchange trading pairs | exchanges: list exchanges from pairs | supported_exchanges: futures exchanges | delisted_pairs: inactive pairs"
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

    elif action == "exchanges":
        data = await client.request("/api/futures/supported-exchange-pairs")
        exchanges = list(data.keys()) if isinstance(data, dict) else []
        return ok(action, exchanges, total=len(exchanges))

    elif action == "supported_exchanges":
        data = await client.request("/api/futures/supported-exchanges")
        return ok(action, data, total=len(data) if isinstance(data, list) else None)

    else:  # delisted_pairs
        data = await client.request("/api/futures/delisted-exchange-pairs")
        return ok(action, data, total=len(data) if isinstance(data, list) else None)


ActionMarketData = Literal[
    "coins_summary",
    "pairs_summary",
    "price_changes",
    "volume_footprint",
]


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
            description="coins_summary: single coin metrics (requires symbol) | pairs_summary: per-pair metrics | price_changes: price % changes across timeframes | volume_footprint: futures footprint snapshots"
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
        "price_changes": "/api/futures/coins-price-change",
        "volume_footprint": "/api/futures/volume/footprint-history",
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
        ENDPOINT_PRICE_OHLC_HISTORY,
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
        Field(
            description=(
                "Exchange for 'pair' action (e.g., 'Binance') or comma-separated "
                "exchange list for 'coin_margin' (e.g., 'Binance,OKX')"
            )
        ),
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
    if action == "coin_margin" and not exchange:
        raise ValueError(
            "Action 'coin_margin' requires exchange list via exchange "
            "(e.g., exchange='Binance,OKX')"
        )
    client = get_client(ctx)

    endpoints = {
        "pair": "/api/futures/open-interest/history",
        "aggregated": ENDPOINT_OI_AGGREGATED_OHLC_HISTORY,
        "stablecoin": "/api/futures/open-interest/aggregated-stablecoin-history",
        "coin_margin": "/api/futures/open-interest/aggregated-coin-margin-history",
    }

    if action == "pair":
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    elif action in ("coin_margin", "stablecoin"):
        params = {
            "symbol": symbol,
            "exchange_list": exchange,
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
    if action == "exchange_chart" and not range:
        raise ValueError("Action 'exchange_chart' requires range (e.g., '7d').")
    client = get_client(ctx)

    if action == "by_exchange":
        data = await client.request(
            "/api/futures/open-interest/exchange-list", {"symbol": symbol}
        )
    else:
        data = await client.request(
            "/api/futures/open-interest/exchange-history-chart",
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
        "pair": "/api/futures/funding-rate/history",
        "oi_weighted": ENDPOINT_FUNDING_OI_WEIGHTED_OHLC_HISTORY,
        "vol_weighted": "/api/futures/funding-rate/vol-weight-history",
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
    range: Annotated[
        Optional[str], Field(description="Time range for accumulated funding (e.g., 24h, 7d)")
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
        "rates": "/api/futures/funding-rate/exchange-list",
        "accumulated": ENDPOINT_FUNDING_ACCUMULATED_HISTORY,
        "arbitrage": "/api/futures/funding-rate/arbitrage",
    }

    if action == "accumulated" and not range:
        raise ValueError(
            "Action 'accumulated' requires range (e.g., '7d' or '30d')."
        )

    params = {"range": range} if action == "accumulated" else {"symbol": symbol}
    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol, range=range)


# ============================================================================
# LONG/SHORT RATIO TOOL (1 tool)
# ============================================================================


ActionLS = Literal[
    "global",
    "top_accounts",
    "top_positions",
    "taker_ratio",
    "net_position",
    "net_position_v2",
]


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
            description="global: global L/S ratio | top_accounts: top traders by account | top_positions: top traders by position | taker_ratio: taker buy/sell ratio | net_position: futures net position history | net_position_v2: v2 net position history"
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
    start_time: Annotated[
        int | None, Field(description="Start timestamp in milliseconds")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp in milliseconds")
    ] = None,
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
        "global": "/api/futures/global-long-short-account-ratio/history",
        "top_accounts": "/api/futures/top-long-short-account-ratio/history",
        "top_positions": "/api/futures/top-long-short-position-ratio/history",
        "taker_ratio": "/api/futures/taker-buy-sell-volume/exchange-list",
        "net_position": "/api/futures/net-position/history",
        "net_position_v2": "/api/futures/v2/net-position/history",
    }

    if action == "taker_ratio":
        params = {"symbol": pair, "range": "24h"}
    elif action in {"net_position", "net_position_v2"}:
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
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


ActionLiqHistory = Literal["pair", "aggregated", "by_coin", "by_exchange", "max_pain"]


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
            description="pair: single pair liquidations | aggregated: by coin | by_coin: coin summary | by_exchange: exchange summary | max_pain: liquidation max pain by range"
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
    range: Annotated[
        str | None, Field(description="Range for by_exchange action: 4h, 12h, 24h")
    ] = None,
    exchange_list: Annotated[
        str | None,
        Field(description="Comma-separated exchanges for aggregated action"),
    ] = None,
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
    if action in {"pair", "aggregated"}:
        check_interval(ctx, interval)
    if action == "pair" and (not exchange or not pair):
        raise ValueError(
            "Action 'pair' requires exchange + pair "
            "(e.g., exchange='Binance', pair='BTCUSDT')."
        )
    if action == "aggregated" and not symbol:
        raise ValueError("Action 'aggregated' requires symbol (e.g., symbol='BTC').")
    client = get_client(ctx)

    endpoints = {
        "pair": "/api/futures/liquidation/history",
        "aggregated": ENDPOINT_LIQ_AGGREGATED_HISTORY,
        "by_coin": "/api/futures/liquidation/coin-list",
        "by_exchange": "/api/futures/liquidation/exchange-list",
        "max_pain": "/api/futures/liquidation/max-pain",
    }

    if action == "pair":
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    elif action == "aggregated":
        params = {
            "exchange_list": (
                exchange_list
                or "Binance,OKX,Bybit,dYdX,Bitget,Huobi,Gate,CoinEx,Kraken,BingX"
            ),
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
    elif action == "by_exchange":
        params = {"range": range or "24h", "symbol": symbol}
    elif action == "max_pain":
        params = {"range": range}
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
    exchange: Annotated[
        str, Field(description="Exchange (e.g., 'Binance')")
    ] = "Binance",
    symbol: Annotated[
        str, Field(description="Coin symbol (e.g., 'BTC')")
    ] = "BTC",
    min_liquidation_amount: Annotated[
        str, Field(description="Minimum liquidation amount (e.g., '10000')")
    ] = "10000",
    start_time: Annotated[
        int | None, Field(description="Start timestamp (seconds)")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp (seconds)")
    ] = None,
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

    params = {
        "exchange": exchange,
        "symbol": symbol,
        "min_liquidation_amount": min_liquidation_amount,
        "start_time": start_time,
        "end_time": end_time,
    }
    data = await client.request("/api/futures/liquidation/order", params)

    return ok("orders", data, exchange=exchange, symbol=symbol)


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
    if action == "pair_map" and not range:
        raise ValueError("Action 'pair_map' requires range (e.g., '7d').")
    client = get_client(ctx)

    pair_heatmap_endpoints = {
        1: "/api/futures/liquidation/heatmap/model1",
        2: "/api/futures/liquidation/heatmap/model2",
        3: "/api/futures/liquidation/heatmap/model3",
    }
    coin_heatmap_endpoints = {
        1: "/api/futures/liquidation/aggregated-heatmap/model1",
        2: "/api/futures/liquidation/aggregated-heatmap/model2",
        3: "/api/futures/liquidation/aggregated-heatmap/model3",
    }

    endpoints = {
        "pair_heatmap": pair_heatmap_endpoints[model],
        "coin_heatmap": coin_heatmap_endpoints[model],
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
    exchange_list: Annotated[
        str | None,
        Field(description="Comma-separated exchanges for coin_depth aggregation"),
    ] = None,
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
    if action == "pair_depth" and (not exchange or not pair):
        raise ValueError(
            "Action 'pair_depth' requires exchange + pair "
            "(e.g., exchange='Binance', pair='BTCUSDT')."
        )
    if action == "coin_depth" and not symbol:
        raise ValueError("Action 'coin_depth' requires symbol (e.g., symbol='BTC').")
    client = get_client(ctx)

    endpoints = {
        "pair_depth": "/api/futures/orderbook/ask-bids-history",
        "coin_depth": ENDPOINT_ORDERBOOK_AGGREGATED_ASK_BIDS_HISTORY,
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
    elif action == "coin_depth":
        params = {
            "exchange_list": (
                exchange_list
                or "Binance,OKX,Bybit,dYdX,Bitget,Huobi,Gate,CoinEx,Kraken,BingX"
            ),
            "symbol": symbol,
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


ActionLargeOrders = Literal[
    "current",
    "history",
    "large_orders",
    "legacy_current",
    "legacy_history",
]


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
        Field(
            description=(
                "current: futures active large orders | history: futures large-order history "
                "| large_orders: cross-market large orders "
                "| legacy_current: /api/orderbook/large-limit-order- "
                "| legacy_history: /api/orderbook/large-limit-order-history-"
            )
        ),
    ],
    exchange: Annotated[
        str | None, Field(description="Filter by exchange")
    ] = None,
    pair: Annotated[str | None, Field(description="Filter by pair")] = None,
    symbol: Annotated[
        str | None,
        Field(description="Symbol for large_orders/legacy_* actions (coin or pair)"),
    ] = None,
    exchanges: Annotated[
        str | None,
        Field(
            description="Comma-separated exchanges for large_orders (e.g., Binance,OKX)"
        ),
    ] = None,
    ex_name: Annotated[
        str | None,
        Field(description="Exchange name for legacy_* actions (maps to exName)"),
    ] = None,
    market_type: Annotated[
        Literal["futures", "spot"] | None,
        Field(description="Market type for large_orders/legacy_* (maps to type)"),
    ] = None,
    start_time: Annotated[
        int | None,
        Field(description="Start timestamp (ms for large_orders; seconds for legacy_history)"),
    ] = None,
    end_time: Annotated[
        int | None,
        Field(description="End timestamp (ms for large_orders; seconds for legacy_history)"),
    ] = None,
    state: Annotated[
        int | None,
        Field(description="Order state for history endpoints (1 in-progress, 2 finish, 3 revoke)"),
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=1000, description="Number of orders")
    ] = 50,
    ctx: Context = None,
) -> dict:
    """Get large limit orders (whale walls).

    Detects significant limit orders that may act as support/resistance.
    Thresholds: BTC >= $1M, ETH >= $500K, others >= $50K.
    CoinGlass `order_side` semantics for large-limit-order payloads:
    - 1 = ask/sell
    - 2 = bid/buy

    Examples:
        - Current whale walls: action="current"
        - Historical large orders: action="history"
    """
    client = get_client(ctx)

    if action == "current":
        resolved_symbol = await resolve_instrument_id(client, exchange, symbol or pair)
        endpoint = "/api/futures/orderbook/large-limit-order"
        params = {"exchange": exchange, "symbol": resolved_symbol, "limit": limit}
    elif action == "history":
        history_symbol = symbol or pair
        missing: list[str] = []
        if not exchange:
            missing.append("exchange")
        if not history_symbol:
            missing.append("symbol")
        if start_time is None:
            missing.append("start_time")
        if end_time is None:
            missing.append("end_time")
        if state is None:
            missing.append("state")
        if missing:
            raise ValueError(
                "Action 'history' requires parameters: " + ", ".join(missing)
            )
        resolved_symbol = await resolve_instrument_id(client, exchange, history_symbol)
        endpoint = "/api/futures/orderbook/large-limit-order-history"
        params = {
            "exchange": exchange,
            "symbol": resolved_symbol,
            "state": state,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
        }
    elif action == "large_orders":
        endpoint = "/api/large-orders"
        params = {
            "exchanges": exchanges,
            "symbol": symbol or pair,
            "type": market_type or "futures",
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit,
        }
    elif action == "legacy_current":
        endpoint = "/api/orderbook/large-limit-order-"
        params = {
            "exName": ex_name or exchange,
            "symbol": symbol or pair,
            "type": market_type or "futures",
        }
    else:  # legacy_history
        endpoint = "/api/orderbook/large-limit-order-history-"
        params = {
            "exName": ex_name or exchange,
            "symbol": symbol or pair,
            "type": market_type or "futures",
            "state": state,
            "limit": limit,
            "startTime": start_time,
            "endTime": end_time,
        }

    data = await client.request(endpoint, params)

    return ok(
        action,
        data,
        exchange=exchange or ex_name,
        pair=pair or symbol,
        exchanges=exchanges,
        type=(
            market_type
            or (
                "futures"
                if action in {"large_orders", "legacy_current", "legacy_history"}
                else None
            )
        ),
    )


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
        "alerts": ENDPOINT_HYPERLIQUID_WHALE_ALERTS,
        "positions": "/api/hyperliquid/whale-position",
        "all_positions": "/api/hyperliquid/position",
    }

    params = {} if action == "alerts" else {"symbol": symbol, "user": user, "page": page}
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
    interval: Annotated[
        str, Field(description="Interval (e.g., '1h', '4h', '1d')")
    ] = "1d",
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
    data = await client.request(
        ENDPOINT_BITFINEX_LONG_SHORT_HISTORY, {"symbol": symbol, "interval": interval}
    )
    return ok("bitfinex_margin", data, symbol=symbol, interval=interval)


# ============================================================================
# TAKER TOOL (1 tool)
# ============================================================================


ActionTaker = Literal["pair_history", "coin_history", "by_exchange", "aggregated_ratio"]


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
            description="pair_history: single pair | coin_history: aggregated | by_exchange: ratio by exchange | aggregated_ratio: aggregated taker buy/sell ratio history"
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
    start_time: Annotated[
        int | None, Field(description="Start timestamp in milliseconds")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp in milliseconds")
    ] = None,
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
            "pair_history": "/api/futures/v2/taker-buy-sell-volume/history",
            "coin_history": "/api/futures/aggregated-taker-buy-sell-volume/history",
            "by_exchange": "/api/futures/taker-buy-sell-volume/exchange-list",
            "aggregated_ratio": "/api/futures/aggregated-taker-buy-sell-volume/history",
        }
    else:
        endpoints = {
            "pair_history": "/api/spot/taker-buy-sell-volume/history",
            "coin_history": "/api/spot/aggregated-taker-buy-sell-volume/history",
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
    elif action == "by_exchange":
        if market != "futures":
            raise ValueError("Action 'by_exchange' is only available for futures market")
        params = {"symbol": symbol, "range": range or "24h"}
    elif action == "aggregated_ratio":
        if market != "futures":
            raise ValueError(
                "Action 'aggregated_ratio' is only available for futures market"
            )
        params = {
            "exchange_list": exchange_list or exchange or "Binance",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
    else:
        raise ValueError(f"Unsupported action '{action}' for market '{market}'")

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
    "orderbook_aggregated_ask_bids_history",
    "orderbook_ask_bids_history",
    "orderbook_history",
    "orderbook_large_limit_order",
    "orderbook_large_limit_order_history",
    "volume_footprint_history",
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
            description=(
                "coins: supported coins | pairs: exchange pairs | coins_markets: coin data "
                "| pairs_markets: pair data | price_history: OHLC "
                "| taker_history: pair taker volume "
                "| taker_aggregated_history: aggregated taker volume "
                "| orderbook_aggregated_ask_bids_history: aggregated spot depth history "
                "| orderbook_ask_bids_history: pair spot depth history "
                "| orderbook_history: spot orderbook heatmap history "
                "| orderbook_large_limit_order: active spot large limit orders "
                "| orderbook_large_limit_order_history: historical spot large limit orders "
                "| volume_footprint_history: spot volume footprint history"
            )
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
    start_time: Annotated[
        int | None, Field(description="Start timestamp in milliseconds")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp in milliseconds")
    ] = None,
    range: Annotated[
        str | None,
        Field(description="Orderbook depth range percentage (e.g., 0.5, 1, 2, 5)"),
    ] = None,
    state: Annotated[
        int | str | None,
        Field(description="Order status for orderbook_large_limit_order_history"),
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
        "price_history": "/api/spot/price/history",
        "taker_history": "/api/spot/taker-buy-sell-volume/history",
        "taker_aggregated_history": "/api/spot/aggregated-taker-buy-sell-volume/history",
        "orderbook_aggregated_ask_bids_history": "/api/spot/orderbook/aggregated-ask-bids-history",
        "orderbook_ask_bids_history": "/api/spot/orderbook/ask-bids-history",
        "orderbook_history": "/api/spot/orderbook/history",
        "orderbook_large_limit_order": "/api/spot/orderbook/large-limit-order",
        "orderbook_large_limit_order_history": "/api/spot/orderbook/large-limit-order-history",
        "volume_footprint_history": "/api/spot/volume/footprint-history",
    }

    if action == "price_history":
        price_interval = interval or "h1"
        check_interval(ctx, price_interval)
        params = {
            "exchange": exchange,
            "symbol": pair or symbol,
            "interval": price_interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
        interval = price_interval
    elif action == "taker_history":
        taker_interval = interval or "h1"
        check_interval(ctx, taker_interval)
        params = {
            "exchange": exchange,
            "symbol": pair or symbol,
            "interval": taker_interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
        interval = taker_interval
    elif action == "taker_aggregated_history":
        taker_interval = interval or "h1"
        check_interval(ctx, taker_interval)
        params = {
            "symbol": symbol,
            "interval": taker_interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
            "exchange_list": (
                exchange_list
                or "Binance,OKX,Coinbase,Bybit,Kraken,Huobi,Gate,Bitfinex,KuCoin"
            ),
        }
        interval = taker_interval
    elif action == "orderbook_aggregated_ask_bids_history":
        orderbook_interval = interval or "h1"
        check_interval(ctx, orderbook_interval)
        params = {
            "exchange_list": (
                exchange_list
                or "Binance,OKX,Coinbase,Bybit,Kraken,Huobi,Gate,Bitfinex,KuCoin"
            ),
            "symbol": symbol,
            "interval": orderbook_interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
            "range": range,
        }
        interval = orderbook_interval
    elif action == "orderbook_ask_bids_history":
        orderbook_interval = interval or "h1"
        check_interval(ctx, orderbook_interval)
        params = {
            "exchange": exchange,
            "symbol": pair or symbol,
            "interval": orderbook_interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
            "range": range,
        }
        interval = orderbook_interval
    elif action == "orderbook_history":
        orderbook_interval = interval or "h1"
        check_interval(ctx, orderbook_interval)
        params = {
            "exchange": exchange,
            "symbol": pair or symbol,
            "interval": orderbook_interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
        interval = orderbook_interval
    elif action == "orderbook_large_limit_order":
        params = {"exchange": exchange, "symbol": pair or symbol}
    elif action == "orderbook_large_limit_order_history":
        params = {
            "exchange": exchange,
            "symbol": pair or symbol,
            "start_time": start_time,
            "end_time": end_time,
            "state": state,
        }
    elif action == "volume_footprint_history":
        params = None
    elif action == "pairs_markets":
        params = {"symbol": symbol}
    else:
        params = {}

    data = await request_with_fallback(client, endpoints[action], params)

    return ok(
        action,
        data,
        symbol=symbol or pair,
        exchange=exchange,
        interval=interval,
        range=range,
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
    exchange: Annotated[
        Optional[str], Field(description="Exchange for max_pain (e.g., Deribit, OKX)")
    ] = None,
    range: Annotated[
        str | None, Field(description="Time range: 7d, 30d, 90d")
    ] = None,
    unit: Annotated[
        Optional[str],
        Field(description="Unit for oi_history action (required by API)"),
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

    if action == "oi_history" and not unit:
        raise ValueError("Action 'oi_history' requires unit.")

    params = {"symbol": symbol}
    if action == "max_pain":
        params["exchange"] = exchange
    if action == "oi_history":
        params["unit"] = unit
    if range:
        params["range"] = range

    data = await client.request(endpoints[action], params)

    return ok(action, data, symbol=symbol, exchange=exchange, range=range, unit=unit)


# ============================================================================
# ON-CHAIN TOOL (1 tool)
# ============================================================================


ActionOnChain = Literal[
    "assets",
    "balance_list",
    "balance_chart",
    "transfers",
    "whale_transfer",
    "assets_transparency",
]


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
            description="assets: exchange holdings | balance_list: balances by asset | balance_chart: historical | transfers: ERC-20 transactions | whale_transfer: large on-chain transfers | assets_transparency: exchange proof-of-assets list"
        ),
    ],
    exchange: Annotated[str | None, Field(description="Exchange filter")] = None,
    asset: Annotated[
        str | None, Field(description="Asset: BTC, ETH, USDT")
    ] = None,
    symbol: Annotated[
        Optional[str],
        Field(description="Symbol filter for assets/balance_list (e.g., BTC, ETH)"),
    ] = None,
    range: Annotated[
        str | None, Field(description="Time range: 7d, 30d, 90d")
    ] = None,
    transfer_type: Annotated[
        Literal["inflow", "outflow", "internal"] | None,
        Field(description="Filter transfers by type"),
    ] = None,
    start_time: Annotated[
        int | None, Field(description="Start timestamp in milliseconds")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp in milliseconds")
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
    if action == "balance_list" and not symbol:
        raise ValueError(
            "Action 'balance_list' requires symbol (e.g., symbol='BTC')."
        )

    endpoints = {
        "assets": "/api/exchange/assets",
        "balance_list": ENDPOINT_ONCHAIN_BALANCE_LIST,
        "balance_chart": "/api/exchange/balance/chart",
        "transfers": "/api/exchange/chain/tx/list",
        "whale_transfer": ENDPOINT_ONCHAIN_WHALE_TRANSFER,
        "assets_transparency": "/api/exchange_assets_transparency/list",
    }

    params = {
        "exchange": exchange,
        "asset": asset,
        "range": range,
        "type": transfer_type,
        "limit": limit,
        "symbol": (
            symbol
            if action in {"assets", "balance_list"}
            else (symbol or asset) if action == "whale_transfer" else None
        ),
        "start_time": start_time if action == "whale_transfer" else None,
        "end_time": end_time if action == "whale_transfer" else None,
    }

    data = await client.request(endpoints[action], params)

    return ok(action, data, exchange=exchange, asset=asset, symbol=symbol)


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
    "ethereum_list",
    "ethereum_flows",
    "ethereum_net_assets",
    "solana_flows",
    "xrp_flows",
    "hk_bitcoin_flows",
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

    base = "/api/etf/bitcoin" if asset == "bitcoin" else "/api/etf/ethereum"
    endpoints: dict[ActionETF, str | list[str]] = {
        "list": f"{base}/list",
        "flows": f"{base}/flow-history",
        "history": f"{base}/history",
        "net_assets": f"{base}/net-assets/history",
        "premium": f"{base}/premium-discount/history",
        "detail": f"{base}/detail",
        "price": f"{base}/price/history",
        "bitcoin_list": "/api/etf/bitcoin/list",
        "bitcoin_flows": "/api/etf/bitcoin/flow-history",
        "bitcoin_history": "/api/etf/bitcoin/history",
        "bitcoin_net_assets": "/api/etf/bitcoin/net-assets/history",
        "bitcoin_premium_discount": "/api/etf/bitcoin/premium-discount/history",
        "bitcoin_detail": "/api/etf/bitcoin/detail",
        "bitcoin_price": "/api/etf/bitcoin/price/history",
        "ethereum_list": "/api/etf/ethereum/list",
        "ethereum_flows": "/api/etf/ethereum/flow-history",
        "ethereum_net_assets": "/api/etf/ethereum/net-assets/history",
        "solana_flows": "/api/etf/solana/flow-history",
        "xrp_flows": "/api/etf/xrp/flow-history",
        "hk_bitcoin_flows": "/api/hk-etf/bitcoin/flow-history",
    }

    explicit_asset = {
        "bitcoin": {
            "bitcoin_list",
            "bitcoin_flows",
            "bitcoin_history",
            "bitcoin_net_assets",
            "bitcoin_premium_discount",
            "bitcoin_detail",
            "bitcoin_price",
            "hk_bitcoin_flows",
        },
        "ethereum": {"ethereum_list", "ethereum_flows", "ethereum_net_assets"},
        "solana": {"solana_flows"},
        "xrp": {"xrp_flows"},
    }

    if action in explicit_asset["bitcoin"]:
        asset_value = "bitcoin"
    elif action in explicit_asset["ethereum"]:
        asset_value = "ethereum"
    elif action in explicit_asset["solana"]:
        asset_value = "solana"
    elif action in explicit_asset["xrp"]:
        asset_value = "xrp"
    else:
        asset_value = asset

    no_param_actions = {
        "ethereum_list",
        "ethereum_flows",
        "ethereum_net_assets",
        "solana_flows",
        "xrp_flows",
        "hk_bitcoin_flows",
    }

    if action in no_param_actions:
        params = None
    else:
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
    symbol: Annotated[
        Optional[str], Field(description="Asset symbol for premium (e.g., BTC, ETH)")
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
        "holdings": "/api/grayscale/holdings-list",
        "premium": "/api/grayscale/premium-history",
    }

    params = {"fund": fund, "symbol": symbol if action == "premium" else None, "range": range}
    data = await request_with_fallback(client, endpoints[action], params)

    return ok(action, data, fund=fund, symbol=symbol, range=range)


# ============================================================================
# INDICATORS TOOL (1 tool)
# ============================================================================


ActionIndicators = Literal[
    "rsi",
    "futures_rsi",
    "futures_ma",
    "futures_ema",
    "futures_macd",
    "futures_boll",
    "basis",
    "coinbase_premium",
    "fear_greed",
    "ahr999",
    "puell",
    "stock_flow",
    "pi_cycle",
    "rainbow",
    "bubble",
    "altcoin_season",
    "bitcoin_active_addresses",
    "bitcoin_correlation",
    "bitcoin_dominance",
    "bitcoin_lth_supply",
    "bitcoin_lth_realized_price",
    "bitcoin_lth_sopr",
    "bitcoin_macro_oscillator",
    "bitcoin_nupl",
    "bitcoin_new_addresses",
    "bitcoin_reserve_risk",
    "bitcoin_rhodl_ratio",
    "bitcoin_sth_supply",
    "bitcoin_sth_realized_price",
    "bitcoin_sth_sopr",
    "bitcoin_vs_global_m2_growth",
    "bitcoin_vs_us_m2_growth",
    "golden_ratio_multiplier",
    "option_vs_futures_oi_ratio",
    "ma_2year",
    "ma_200week",
    "profitable_days",
    "stablecoin_mcap",
    "bull_peak",
    "borrow_rate",
    "whale_index",
    "cdri_index",
    "cgdi_index",
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
            description="Market indicator: rsi, futures_rsi/futures_ma/futures_ema/futures_macd/futures_boll, basis, coinbase_premium, fear_greed, ahr999, puell, stock_flow, pi_cycle, rainbow, bubble, ma_2year, ma_200week, profitable_days, stablecoin_mcap, bull_peak, borrow_rate, whale_index, cdri_index, cgdi_index"
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
    start_time: Annotated[
        int | None, Field(description="Start timestamp in milliseconds")
    ] = None,
    end_time: Annotated[
        int | None, Field(description="End timestamp in milliseconds")
    ] = None,
    series_type: Annotated[
        Literal["open", "high", "low", "close"] | None,
        Field(description="Series type for futures_* indicators"),
    ] = None,
    window: Annotated[
        int | None, Field(description="Window for futures_rsi/ma/ema/boll")
    ] = None,
    mult: Annotated[
        float | None, Field(description="Band multiplier for futures_boll")
    ] = None,
    fast_window: Annotated[
        int | None, Field(description="Fast window for futures_macd")
    ] = None,
    slow_window: Annotated[
        int | None, Field(description="Slow window for futures_macd")
    ] = None,
    signal_window: Annotated[
        int | None, Field(description="Signal window for futures_macd")
    ] = None,
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
        "rsi": "/api/futures/rsi/list",
        "futures_rsi": "/api/futures/indicators/rsi",
        "futures_ma": "/api/futures/indicators/ma",
        "futures_ema": "/api/futures/indicators/ema",
        "futures_macd": "/api/futures/indicators/macd",
        "futures_boll": "/api/futures/indicators/boll",
        "basis": "/api/futures/basis/history",
        "coinbase_premium": "/api/coinbase-premium-index",
        "fear_greed": "/api/index/fear-greed-history",
        "ahr999": "/api/index/ahr999",
        "puell": "/api/index/puell-multiple",
        "stock_flow": "/api/index/stock-flow",
        "pi_cycle": "/api/index/pi-cycle-indicator",
        "rainbow": "/api/index/bitcoin/rainbow-chart",
        "bubble": "/api/index/bitcoin/bubble-index",
        "altcoin_season": "/api/index/altcoin-season",
        "bitcoin_active_addresses": "/api/index/bitcoin-active-addresses",
        "bitcoin_correlation": "/api/index/bitcoin-correlation",
        "bitcoin_dominance": "/api/index/bitcoin-dominance",
        "bitcoin_lth_supply": "/api/index/bitcoin-long-term-holder-supply",
        "bitcoin_lth_realized_price": "/api/index/bitcoin-lth-realized-price",
        "bitcoin_lth_sopr": "/api/index/bitcoin-lth-sopr",
        "bitcoin_macro_oscillator": "/api/index/bitcoin-macro-oscillator",
        "bitcoin_nupl": "/api/index/bitcoin-net-unrealized-profit-loss",
        "bitcoin_new_addresses": "/api/index/bitcoin-new-addresses",
        "bitcoin_reserve_risk": "/api/index/bitcoin-reserve-risk",
        "bitcoin_rhodl_ratio": "/api/index/bitcoin-rhodl-ratio",
        "bitcoin_sth_supply": "/api/index/bitcoin-short-term-holder-supply",
        "bitcoin_sth_realized_price": "/api/index/bitcoin-sth-realized-price",
        "bitcoin_sth_sopr": "/api/index/bitcoin-sth-sopr",
        "bitcoin_vs_global_m2_growth": "/api/index/bitcoin-vs-global-m2-growth",
        "bitcoin_vs_us_m2_growth": "/api/index/bitcoin-vs-us-m2-growth",
        "golden_ratio_multiplier": "/api/index/golden-ratio-multiplier",
        "option_vs_futures_oi_ratio": "/api/index/option-vs-futures-oi-ratio",
        "ma_2year": "/api/index/2-year-ma-multiplier",
        "ma_200week": "/api/index/200-week-moving-average-heatmap",
        "profitable_days": "/api/index/bitcoin/profitable-days",
        "stablecoin_mcap": "/api/index/stableCoin-marketCap-history",
        "bull_peak": "/api/bull-market-peak-indicator",
        "borrow_rate": "/api/borrow-interest-rate/history",
        "whale_index": "/api/futures/whale-index/history",
        "cdri_index": [
            "/api/futures/cdri-index/history",
            "/api/futures/cdri-index/history ",
        ],
        "cgdi_index": [
            "/api/futures/cgdi-index/history",
            "/api/futures/cgdi-index/history  ",
        ],
    }

    if action in {
        "futures_rsi",
        "futures_ma",
        "futures_ema",
        "futures_macd",
        "futures_boll",
    }:
        if not exchange or not symbol or not interval:
            raise ValueError(
                f"Action '{action}' requires exchange + symbol + interval "
                "(e.g., exchange='Binance', symbol='BTCUSDT', interval='h1')"
            )
        params = {
            "exchange": exchange,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
            "series_type": series_type,
            "window": window,
            "mult": mult,
            "fast_window": fast_window,
            "slow_window": slow_window,
            "signal_window": signal_window,
        }
    elif action in {"cdri_index", "cgdi_index"}:
        params = None
    else:
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
            "actions": [
                "current",
                "history",
                "large_orders",
                "legacy_current",
                "legacy_history",
            ],
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
                "orderbook_aggregated_ask_bids_history",
                "orderbook_ask_bids_history",
                "orderbook_history",
                "orderbook_large_limit_order",
                "orderbook_large_limit_order_history",
                "volume_footprint_history",
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
