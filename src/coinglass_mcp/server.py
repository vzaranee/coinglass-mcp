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
import inspect
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, Optional

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
from pydantic import Field

from coinglass_mcp.client import CoinGlassClient
from coinglass_mcp.config import (
    ACTION_PARAMS,
    ACTION_PLAN,
    PLAN_FEATURES,
    PLAN_HIERARCHY,
    PLAN_INTERVALS,
)
from coinglass_mcp import formatters


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

26 tools wrapping 143 CoinGlass API v4 endpoints. All responses are formatted
(raw JSON compressed to ~1-3KB structured summaries).
Outputs use compact previews by design; see response metadata for
truncation details (`truncated`, `shown_rows`, `total_rows`, `requested_limit`,
`filters_applied`, `truncation_reason`).

Quick start:
- Market overview: coinglass_market_data(action="coins_summary")
- OI by exchange: coinglass_oi_distribution(action="by_exchange", symbol="ETH")
- Funding rates: coinglass_funding_current(action="rates", symbol="BTC")
- Liquidation heatmap: coinglass_liq_heatmap(action="pair_heatmap", symbol="SOL")
- Whale positions: coinglass_whale_positions(action="positions", symbol="ETH")
- ETF flows: coinglass_etf(action="bitcoin_flows")
- Fear & Greed: coinglass_indicators(action="fear_greed")
- Arbitrage: coinglass_funding_current(action="arbitrage")

Use coinglass_search(query="...") to discover tools by keyword.""",
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


def _as_int(value: Any) -> int | None:
    """Best-effort conversion to int."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(float(stripped))
        except ValueError:
            return None
    return None


def _normalize_filter_list(value: Any) -> list[str]:
    """Normalize filter metadata into compact list[str]."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


class StaticBearerTokenVerifier(TokenVerifier):
    """Simple bearer token verifier for optional SSE auth."""

    def __init__(self, token: str):
        super().__init__()
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not secrets.compare_digest(token, self._token):
            return None
        return AccessToken(token=token, client_id="static-bearer", scopes=[])


def ok(action: str, data: Any, **meta: Any) -> dict:
    """Create formatted success response with compact text for LLM consumption."""
    caller = inspect.currentframe().f_back
    tool_name = caller.f_code.co_name if caller else "unknown_tool"

    requested_limit = _as_int(meta.get("requested_limit", meta.get("limit")))
    filters_applied = _normalize_filter_list(meta.get("filters_applied"))
    total_known = _as_int(
        meta.get(
            "total_known",
            meta.get("total", meta.get("count", meta.get("total_rows"))),
        )
    )

    try:
        text, preview_meta = formatters.format_tool_response_with_meta(
            tool_name,
            action,
            data,
            requested_limit=requested_limit,
            filters_applied=filters_applied,
            total_known=total_known,
        )
    except Exception as exc:
        text = formatters.format_json_fallback(tool_name, action, data, reason=str(exc))
        preview_meta = {
            "truncated": True,
            "requested_limit": requested_limit,
            "shown_rows": None,
            "total_rows": None,
            "total_known": total_known,
            "filters_applied": filters_applied,
            "truncation_reason": "formatter_fallback",
        }

    if preview_meta.get("total_known") is None:
        preview_meta["total_known"] = total_known

    return {"text": text, "metadata": preview_meta}


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


def _match_exchange_name(name: Any, expected_exchange: str | None) -> bool:
    """Case/punctuation-insensitive exchange matcher."""
    if not expected_exchange:
        return True
    if name is None:
        return False
    return _normalize_exchange_key(str(name)) == _normalize_exchange_key(
        expected_exchange
    )


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


def _matches_base_symbol(candidate: Any, base_symbol: str | None) -> bool:
    """Best-effort base-asset matcher for symbols/pairs/instrument ids."""
    if not base_symbol:
        return True
    if candidate is None:
        return False

    needle = base_symbol.strip().upper()
    if not needle:
        return True

    text = str(candidate).strip().upper()
    if not text:
        return False
    if text == needle:
        return True

    extracted = _extract_base_quote(text)
    if extracted:
        base, _ = extracted
        if base == needle:
            return True
        # Handle nested variants like BTCUSDT_UMCBL.
        nested = _extract_base_quote(base)
        return bool(nested and nested[0] == needle)

    # Fallback for tokenized strings without a recognizable quote suffix.
    tokenized = text.replace("/", "-").replace("_", "-")
    return needle in {part for part in tokenized.split("-") if part}


def _spot_pair_row_matches(
    row: Any, *, exchange: str | None = None, symbol: str | None = None
) -> bool:
    """Client-side filter for spot supported pairs payload variants."""
    if isinstance(row, dict):
        exchange_ok = (
            _match_exchange_name(_pick_exchange(row), exchange)
            if exchange
            else True
        )
        symbol_ok = (
            _matches_base_symbol(_pick_symbol(row), symbol) if symbol else True
        )
        return exchange_ok and symbol_ok

    return _matches_base_symbol(row, symbol)


def _pick_exchange(row: dict[str, Any]) -> Any:
    return (
        row.get("exchange")
        or row.get("exchange_name")
        or row.get("exchangeName")
        or row.get("exName")
        or row.get("ex_name")
        or row.get("key")
    )


def _pick_symbol(row: dict[str, Any]) -> Any:
    return (
        row.get("base_asset")
        or row.get("baseAsset")
        or row.get("symbol")
        or row.get("pair")
        or row.get("instrument_id")
        or row.get("instrumentId")
    )


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
        bitget_swap = f"{base}{quote}_UMCBL"

        # Explicit exchange formats:
        # Binance/Bybit: ETHUSDT
        # OKX: ETH-USDT-SWAP
        # Gate/MEXC: ETH_USDT
        # HTX: ETH-USDT
        # Bitget: ETHUSDT_UMCBL
        exchange_formats: dict[str, list[str]] = {
            "binance": [compact],
            "bybit": [compact],
            "okx": [okx_swap, dashed],
            "gate": [underscored],
            "mexc": [underscored],
            "htx": [dashed],
            "huobi": [dashed],
            "bitget": [bitget_swap, compact],
        }

        preferred = exchange_formats.get(exchange_key, [compact])
        candidates.extend(preferred)
        candidates.extend([compact, dashed, underscored, okx_swap, bitget_swap])

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
ENDPOINT_FUTURES_AGGREGATED_TAKER_RATIO_HISTORY = (
    "/api/futures/aggregated-taker-buy-sell-volume/history"
)
# These paths include trailing spaces in the published OpenAPI spec.
ENDPOINT_CDRI_INDEX_HISTORY = "/api/futures/cdri-index/history "
ENDPOINT_CGDI_INDEX_HISTORY = "/api/futures/cgdi-index/history  "


# ============================================================================
# CALENDAR TOOL (1 tool)
# ============================================================================


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
    """Get CoinGlass calendar and macro-event data.

    Returns upcoming economic events, central bank activities, and financial events.

    Examples:
        - Central bank events: action="central_bank_activities"
        - Macro releases: action="economic_data", language="en"
        - Market events: action="financial_events"
    """
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
    symbol: Annotated[
        str | None, Field(description="Filter pairs by base coin (e.g., 'ETH', 'BTC')")
    ] = None,
    ctx: Context = None,
) -> dict:
    """Get static market metadata from CoinGlass.

    Returns lists of supported coins, trading pairs by exchange, or exchanges.
    This data is relatively static and cached for 5 minutes.

    Examples:
        - Get all futures coins: action="coins"
        - Get Binance pairs: action="pairs", exchange="Binance"
        - Get ETH pairs on Binance: action="pairs", exchange="Binance", symbol="ETH"
        - List all exchanges: action="exchanges"
    """
    client = get_client(ctx)

    if action == "coins":
        data = await client.request("/api/futures/supported-coins")
        return ok(action, data, total=len(data) if isinstance(data, list) else None)

    elif action == "pairs":
        data = await client.request(
            "/api/futures/supported-exchange-pairs",
            {"exchange": exchange} if exchange else None,
        )
        filters_applied: list[str] = []
        if exchange:
            filters_applied.append(f"exchange={exchange}")
        # Client-side symbol filter for pairs
        if symbol:
            if isinstance(data, dict):
                filtered_map: dict[str, list[Any]] = {}
                for ex_name, rows in data.items():
                    if exchange and not _match_exchange_name(ex_name, exchange):
                        continue
                    if not isinstance(rows, list):
                        continue
                    filtered_rows = [
                        row for row in rows if _spot_pair_row_matches(row, symbol=symbol)
                    ]
                    if filtered_rows:
                        filtered_map[ex_name] = filtered_rows
                data = filtered_map
            elif isinstance(data, list):
                data = [
                    row
                    for row in data
                    if _spot_pair_row_matches(row, exchange=exchange, symbol=symbol)
                ]
            filters_applied.append(f"symbol={symbol}")
        return ok(
            action,
            data,
            exchange=exchange,
            symbol=symbol,
            filters_applied=filters_applied,
        )

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
            description="coins_summary: single coin metrics (REQUIRES symbol) | pairs_summary: per-pair metrics (defaults to BTC if symbol omitted) | price_changes: price % changes across timeframes | volume_footprint: futures footprint snapshots"
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

    Notes:
        - coins_summary requires symbol.
        - pairs_summary defaults to BTC when symbol is omitted.

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

    if action == "pairs_summary":
        params = {"symbol": symbol or "BTC"}
    else:
        params = None

    data = await request_with_fallback(client, endpoints[action], params)

    if action == "coins_summary" and symbol and isinstance(data, list):
        needle = symbol.upper()
        data = next(
            (
                item
                for item in data
                if isinstance(item, dict)
                and str(item.get("symbol", "")).upper() == needle
            ),
            {},
        )

    filters_applied = [f"symbol={symbol}"] if action == "coins_summary" and symbol else []
    return ok(
        action,
        data,
        symbol=symbol,
        total=len(data) if isinstance(data, list) else None,
        filters_applied=filters_applied,
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
        requested_limit=limit,
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
    if action in {"coin_margin", "stablecoin"} and not exchange:
        raise ValueError(
            f"Action '{action}' requires exchange list via exchange "
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
        requested_limit=limit,
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
        requested_limit=limit,
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
        str | None, Field(description="Legacy coin filter (not used by v4 rates endpoint)")
    ] = None,
    range: Annotated[
        Optional[str], Field(description="Time range for accumulated funding (e.g., 24h, 7d)")
    ] = None,
    usd: Annotated[
        int | None, Field(description="Required for arbitrage action")
    ] = None,
    exchange_list: Annotated[
        str | None, Field(description="Optional exchanges for arbitrage action")
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
    if action == "arbitrage" and usd is None:
        raise ValueError("Action 'arbitrage' requires usd (e.g., usd=10000).")

    if action == "rates":
        params = None
    elif action == "accumulated":
        params = {"range": range}
    else:
        params = {"usd": usd, "exchange_list": exchange_list}
    data = await client.request(endpoints[action], params)
    filters_applied: list[str] = []
    if action in ("rates", "accumulated") and symbol and isinstance(data, list):
        data = [x for x in data if isinstance(x, dict) and x.get("symbol") == symbol]
        filters_applied.append(f"symbol={symbol}")

    return ok(
        action,
        data,
        symbol=symbol,
        range=range,
        usd=usd,
        exchange_list=exchange_list,
        filters_applied=filters_applied,
    )


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
        "taker_ratio": ENDPOINT_FUTURES_AGGREGATED_TAKER_RATIO_HISTORY,
        "net_position": "/api/futures/net-position/history",
        "net_position_v2": "/api/futures/v2/net-position/history",
    }

    if action == "taker_ratio":
        params = {
            "exchange_list": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
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
        requested_limit=limit,
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
    if action == "by_coin" and not exchange:
        raise ValueError("Action 'by_coin' requires exchange (e.g., exchange='Binance').")
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
    elif action == "by_coin":
        params = {"exchange": exchange}
    else:
        params = {}

    data = await client.request(endpoints[action], params)
    filters_applied: list[str] = []
    if action == "max_pain" and symbol and isinstance(data, list):
        data = [x for x in data if isinstance(x, dict) and x.get("symbol") == symbol]
        filters_applied.append(f"symbol={symbol}")

    return ok(
        action,
        data,
        symbol=symbol or pair,
        requested_limit=limit if action in {"pair", "aggregated"} else None,
        filters_applied=filters_applied,
    )


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
    if action == "heatmap" and (not exchange or not (pair or symbol)):
        raise ValueError(
            "Action 'heatmap' requires exchange + symbol/pair "
            "(e.g., exchange='Binance', pair='BTCUSDT')."
        )
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
            "exchange": exchange,
            "symbol": pair or symbol,
            "interval": interval,
            "limit": limit,
        }

    data = await client.request(endpoints[action], params)

    return ok(
        action,
        data,
        symbol=symbol or pair,
        interval=interval,
        range=range,
        requested_limit=limit,
    )


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
                "current: futures active large orders (requires symbol or pair) "
                "| history: futures large-order history (requires exchange,symbol,start_time,end_time,state) "
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
        Field(description="Symbol/pair for current/history/large_orders/legacy_* actions"),
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
    This is counter-intuitive versus common BUY=1 conventions.

    Examples:
        - Current whale walls: action="current"
        - Historical large orders: action="history"
    """
    client = get_client(ctx)

    if action == "current":
        current_symbol = symbol or pair
        if not exchange:
            exchange = "Binance"  # sensible default for large orders
        if not current_symbol:
            raise ValueError("Action 'current' requires symbol or pair (e.g., symbol='ETHUSDT').")
        resolved_symbol = await resolve_instrument_id(client, exchange, current_symbol)
        endpoint = "/api/futures/orderbook/large-limit-order"
        params = {"exchange": exchange, "symbol": resolved_symbol}
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
        # /api/large-orders is deprecated (404). Redirect to current endpoint.
        large_exchange = exchange or (exchanges.split(",")[0] if exchanges else "Binance")
        large_symbol = symbol or pair
        if not large_symbol:
            raise ValueError("Action 'large_orders' requires symbol/pair (e.g., symbol='BTCUSDT').")
        resolved_symbol = await resolve_instrument_id(client, large_exchange, large_symbol)
        endpoint = "/api/futures/orderbook/large-limit-order"
        params = {"exchange": large_exchange, "symbol": resolved_symbol}
    elif action == "legacy_current":
        if not (ex_name or exchange) or not (symbol or pair):
            raise ValueError(
                "Action 'legacy_current' requires ex_name/exchange + symbol/pair."
            )
        endpoint = "/api/orderbook/large-limit-order-"
        params = {
            "exName": ex_name or exchange,
            "symbol": symbol or pair,
            "type": market_type or "futures",
        }
    else:  # legacy_history
        if not (ex_name or exchange) or not (symbol or pair):
            raise ValueError(
                "Action 'legacy_history' requires ex_name/exchange + symbol/pair."
            )
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

    requested_limit = limit if action in {"history", "legacy_history"} else None
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
        requested_limit=requested_limit,
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

    if action == "alerts":
        params = {"user": user} if user else None
    elif action == "positions":
        params = {"user": user} if user else None
    else:
        params = {"current_page": str(page)}
        if symbol:
            params["symbol"] = symbol
        if user:
            params["user"] = user
    data = await client.request(endpoints[action], params)

    # Client-side symbol filter for positions (API returns all coins)
    if symbol and action == "positions" and isinstance(data, list):
        sym_upper = symbol.upper()
        data = [r for r in data if str(r.get("symbol", "")).upper() == sym_upper]

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
            "aggregated_ratio": ENDPOINT_FUTURES_AGGREGATED_TAKER_RATIO_HISTORY,
        }
    else:
        endpoints = {
            "pair_history": "/api/spot/taker-buy-sell-volume/history",
            "coin_history": "/api/spot/aggregated-taker-buy-sell-volume/history",
        }

    if action == "pair_history":
        if not exchange or not pair:
            raise ValueError("Action 'pair_history' requires exchange + pair.")
        params = {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        }
    elif action == "coin_history":
        if not symbol:
            raise ValueError("Action 'coin_history' requires symbol.")
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
        if not symbol:
            raise ValueError("Action 'by_exchange' requires symbol.")
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
        requested_limit=(
            limit
            if action in {"pair_history", "coin_history", "aggregated_ratio"}
            else None
        ),
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
                "coins: supported coins | pairs: exchange pairs (supports exchange/symbol filters) "
                "| coins_markets: coin data "
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
    symbol: Annotated[
        str | None,
        Field(description="Coin/base-asset filter (e.g., BTC, ETH); required for pairs_markets"),
    ] = None,
    exchange: Annotated[
        str | None,
        Field(description="Exchange (required for price_history/taker_history; optional filter for pairs)"),
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

    filters_applied: list[str] = []

    if action == "price_history":
        price_interval = interval or "h1"
        check_interval(ctx, price_interval)
        if not exchange or not (pair or symbol):
            raise ValueError(
                "Action 'price_history' requires exchange + pair/symbol."
            )
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
        if not exchange or not (pair or symbol):
            raise ValueError(
                "Action 'taker_history' requires exchange + pair/symbol."
            )
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
        if not symbol:
            raise ValueError(
                "Action 'taker_aggregated_history' requires symbol (e.g., BTCUSDT)."
            )
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
        if not symbol:
            raise ValueError(
                "Action 'orderbook_aggregated_ask_bids_history' requires symbol."
            )
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
        if not exchange or not (pair or symbol):
            raise ValueError(
                "Action 'orderbook_ask_bids_history' requires exchange + pair/symbol."
            )
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
        if not exchange or not (pair or symbol):
            raise ValueError(
                "Action 'orderbook_history' requires exchange + pair/symbol."
            )
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
        if not exchange or not (pair or symbol):
            raise ValueError(
                "Action 'orderbook_large_limit_order' requires exchange + pair/symbol."
            )
        params = {"exchange": exchange, "symbol": pair or symbol}
    elif action == "orderbook_large_limit_order_history":
        if not exchange or not (pair or symbol):
            raise ValueError(
                "Action 'orderbook_large_limit_order_history' requires exchange + pair/symbol."
            )
        if start_time is None or end_time is None or state is None:
            raise ValueError(
                "Action 'orderbook_large_limit_order_history' requires start_time, end_time, and state."
            )
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
        if not symbol:
            raise ValueError("Action 'pairs_markets' requires symbol.")
        params = {"symbol": symbol}
    elif action == "pairs":
        # Upstream spot pairs endpoint can return empty for exchange-filtered queries.
        # Always fetch full payload and apply exchange/symbol filters client-side.
        params = {}
    else:
        params = {}

    data = await request_with_fallback(client, endpoints[action], params)

    # Client-side filtering for endpoints with broad payloads.
    if symbol and action == "coins_markets" and isinstance(data, list):
        sym_upper = symbol.upper()
        filtered = [r for r in data if str(r.get("symbol", "")).upper() == sym_upper]
        data = filtered
        filters_applied.append(f"symbol={symbol}")
    if action == "pairs" and (exchange or symbol):
        if exchange:
            filters_applied.append(f"exchange={exchange}")
        if symbol:
            filters_applied.append(f"symbol={symbol}")

        if isinstance(data, dict):
            filtered_map: dict[str, list[Any]] = {}
            for ex_name, rows in data.items():
                if exchange and not _match_exchange_name(ex_name, exchange):
                    continue
                if not isinstance(rows, list):
                    continue
                filtered_rows = [
                    row
                    for row in rows
                    if _spot_pair_row_matches(row, symbol=symbol)
                ]
                if filtered_rows:
                    filtered_map[ex_name] = filtered_rows
            data = filtered_map
        elif isinstance(data, list):
            data = [
                row
                for row in data
                if _spot_pair_row_matches(row, exchange=exchange, symbol=symbol)
            ]

    requested_limit = (
        limit
        if action
        in {
            "price_history",
            "taker_history",
            "taker_aggregated_history",
            "orderbook_aggregated_ask_bids_history",
            "orderbook_ask_bids_history",
            "orderbook_history",
        }
        else None
    )
    return ok(
        action,
        data,
        symbol=symbol or pair,
        exchange=exchange,
        interval=interval,
        range=range,
        requested_limit=requested_limit,
        filters_applied=filters_applied,
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
        str, Field(description="Coin symbol (e.g., 'BTC', 'ETH', 'SOL')")
    ],
    exchange: Annotated[
        Optional[str], Field(description="Exchange for max_pain action (defaults to Deribit if omitted). Options: Deribit, OKX, Binance, Bybit, CME")
    ] = None,
    range: Annotated[
        str | None, Field(description="Time range for oi_history: 1h, 4h, 12h, all (defaults to 4h). For other tools: 7d, 30d, 90d")
    ] = None,
    unit: Annotated[
        Optional[str],
        Field(description="Unit for oi_history/volume_history (defaults to USD). For BTC use USD or BTC, for ETH use USD or ETH"),
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

    if action == "max_pain" and not exchange:
        exchange = "Deribit"  # Default to largest options exchange
    if action == "oi_history":
        unit = unit or "USD"
        range = range or "4h"  # API supports: 1h, 4h, 12h, all
    if action == "volume_history":
        unit = unit or "USD"

    if action == "max_pain":
        params = {"symbol": symbol, "exchange": exchange}
    elif action == "info":
        params = {"symbol": symbol}
    elif action == "oi_history":
        params = {"symbol": symbol, "unit": unit, "range": range}
    else:
        params = {"symbol": symbol, "unit": unit}

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
            description="assets: exchange holdings (REQUIRES exchange) | balance_list: balances by asset | balance_chart: historical | transfers: ERC-20 transactions | whale_transfer: large on-chain transfers | assets_transparency: exchange proof-of-assets list"
        ),
    ],
    exchange: Annotated[
        str | None,
        Field(description="Exchange filter (required for assets action)"),
    ] = None,
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
    min_usd: Annotated[
        float | None, Field(description="Minimum USD threshold for transfers")
    ] = None,
    per_page: Annotated[
        int | None, Field(ge=1, description="Items per page")
    ] = None,
    page: Annotated[
        int | None, Field(ge=1, description="Page number")
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
    base_symbol = symbol or asset

    endpoints = {
        "assets": "/api/exchange/assets",
        "balance_list": ENDPOINT_ONCHAIN_BALANCE_LIST,
        "balance_chart": "/api/exchange/balance/chart",
        "transfers": "/api/exchange/chain/tx/list",
        "whale_transfer": ENDPOINT_ONCHAIN_WHALE_TRANSFER,
        "assets_transparency": "/api/exchange_assets_transparency/list",
    }

    if action == "assets":
        if not exchange:
            raise ValueError("Action 'assets' requires exchange.")
        params = {
            "exchange": exchange,
            "per_page": str(per_page) if per_page is not None else None,
            "page": str(page) if page is not None else None,
        }
    elif action == "balance_list":
        if not base_symbol:
            raise ValueError("Action 'balance_list' requires symbol.")
        params = {"symbol": base_symbol}
    elif action == "balance_chart":
        if not base_symbol:
            raise ValueError("Action 'balance_chart' requires symbol.")
        params = {"symbol": base_symbol}
    elif action == "transfers":
        params = {
            "symbol": base_symbol,
            "start_time": start_time,
            "min_usd": min_usd,
            "per_page": per_page,
            "page": page,
        }
    elif action == "whale_transfer":
        params = {
            "symbol": base_symbol,
            "start_time": start_time,
            "end_time": end_time,
        }
    else:
        params = None

    data = await client.request(endpoints[action], params)

    return ok(action, data, exchange=exchange, asset=asset, symbol=base_symbol)


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
        str | None, Field(description="Asset for generic actions: bitcoin, ethereum, solana, xrp, hk")
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
    requested_asset = (asset or "bitcoin").strip().lower() or "bitcoin"

    generic_list_endpoints = {
        "bitcoin": "/api/etf/bitcoin/list",
        "ethereum": "/api/etf/ethereum/list",
    }
    generic_flows_endpoints = {
        "bitcoin": "/api/etf/bitcoin/flow-history",
        "ethereum": "/api/etf/ethereum/flow-history",
        "solana": "/api/etf/solana/flow-history",
        "xrp": "/api/etf/xrp/flow-history",
        "hk": "/api/hk-etf/bitcoin/flow-history",
    }
    generic_net_assets_endpoints = {
        "bitcoin": "/api/etf/bitcoin/net-assets/history",
        "ethereum": "/api/etf/ethereum/net-assets/history",
    }

    if action == "list" and requested_asset not in generic_list_endpoints:
        raise ValueError(
            "Action 'list' supports asset='bitcoin' or asset='ethereum'."
        )
    if action == "flows" and requested_asset not in generic_flows_endpoints:
        raise ValueError(
            "Action 'flows' supports asset='bitcoin', 'ethereum', 'solana', 'xrp', or 'hk'."
        )
    if action == "net_assets" and requested_asset not in generic_net_assets_endpoints:
        raise ValueError(
            "Action 'net_assets' supports asset='bitcoin' or asset='ethereum'."
        )
    if action in {"history", "premium", "detail", "price"} and requested_asset != "bitcoin":
        raise ValueError(f"Action '{action}' is only available for asset='bitcoin'.")

    endpoints: dict[ActionETF, str] = {
        "list": generic_list_endpoints.get(requested_asset, generic_list_endpoints["bitcoin"]),
        "flows": generic_flows_endpoints.get(requested_asset, generic_flows_endpoints["bitcoin"]),
        "history": "/api/etf/bitcoin/history",
        "net_assets": generic_net_assets_endpoints.get(
            requested_asset, generic_net_assets_endpoints["bitcoin"]
        ),
        "premium": "/api/etf/bitcoin/premium-discount/history",
        "detail": "/api/etf/bitcoin/detail",
        "price": "/api/etf/bitcoin/price/history",
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
        asset_value = requested_asset

    if action in {"history", "bitcoin_history", "detail", "bitcoin_detail"} and not ticker:
        raise ValueError(f"Action '{action}' requires ticker (e.g., ticker='IBIT').")
    if action in {"price", "bitcoin_price"} and (not ticker or not range):
        raise ValueError(f"Action '{action}' requires ticker + range.")

    if action in {
        "list",
        "flows",
        "bitcoin_list",
        "bitcoin_flows",
        "ethereum_list",
        "ethereum_flows",
        "ethereum_net_assets",
        "solana_flows",
        "xrp_flows",
        "hk_bitcoin_flows",
    }:
        params = None
    elif action in {"history", "bitcoin_history", "detail", "bitcoin_detail"}:
        params = {"ticker": ticker}
    elif action in {"price", "bitcoin_price"}:
        params = {"ticker": ticker, "range": range}
    elif action in {"premium", "bitcoin_premium_discount", "bitcoin_net_assets"}:
        params = {"ticker": ticker}
    elif action == "net_assets":
        params = {"ticker": ticker} if requested_asset == "bitcoin" else None
    else:
        params = None

    data = await request_with_fallback(client, endpoints[action], params)

    return ok(action, data, asset=asset_value, ticker=ticker, range=range)


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

    endpoints: dict[ActionIndicators, str] = {
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
        "cdri_index": ENDPOINT_CDRI_INDEX_HISTORY,
        "cgdi_index": ENDPOINT_CGDI_INDEX_HISTORY,
    }

    futures_indicator_actions = {
        "futures_rsi",
        "futures_ma",
        "futures_ema",
        "futures_macd",
        "futures_boll",
    }
    base_series_params = {
        "exchange": exchange,
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
        "start_time": start_time,
        "end_time": end_time,
    }

    if action in futures_indicator_actions:
        if not exchange or not symbol or not interval:
            raise ValueError(
                f"Action '{action}' requires exchange + symbol + interval "
                "(e.g., exchange='Binance', symbol='BTCUSDT', interval='h1')"
            )
        check_interval(ctx, interval)
        if action in {"futures_rsi", "futures_ma", "futures_ema"}:
            params = {
                **base_series_params,
                "window": window,
                "series_type": series_type,
            }
        elif action == "futures_macd":
            params = {
                **base_series_params,
                "series_type": series_type,
                "fast_window": fast_window,
                "slow_window": slow_window,
                "signal_window": signal_window,
            }
        else:
            params = {
                **base_series_params,
                "series_type": series_type,
                "window": window,
                "mult": mult,
            }
    elif action in {"basis", "borrow_rate", "whale_index"}:
        if not exchange or not symbol or not interval:
            raise ValueError(
                f"Action '{action}' requires exchange + symbol + interval."
            )
        check_interval(ctx, interval)
        params = {
            "exchange": exchange,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
    elif action == "coinbase_premium":
        if not interval:
            raise ValueError("Action 'coinbase_premium' requires interval.")
        check_interval(ctx, interval)
        params = {
            "interval": interval,
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
        }
    elif action in {"cdri_index", "cgdi_index"}:
        params = None
    elif action == "rsi":
        params = None
    else:
        params = None

    data = await request_with_fallback(client, endpoints[action], params)

    # Client-side symbol filtering for list endpoints (rsi, etc.)
    if symbol and action == "rsi" and isinstance(data, list):
        sym_upper = symbol.upper()
        filtered = [r for r in data if str(r.get("symbol", "")).upper() == sym_upper]
        if filtered:
            data = filtered

    return ok(
        action,
        data,
        symbol=symbol,
        requested_limit=(
            limit
            if action
            in {
                "futures_rsi",
                "futures_ma",
                "futures_ema",
                "futures_macd",
                "futures_boll",
                "basis",
                "borrow_rate",
                "whale_index",
                "coinbase_premium",
            }
            else None
        ),
    )


# ============================================================================
# META TOOLS (2 tools)
# ============================================================================


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
        client = get_client(ctx)
        futures_ex = await client.request("/api/futures/supported-exchanges")
        # Spot and options don't have dedicated list endpoints — use futures as reference
        data = {
            "futures": futures_ex if isinstance(futures_ex, list) else [],
            "note": "Use coinglass_market_info(action='exchanges') for full pair-level exchange list",
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
    """Run the CoinGlass MCP server.

    Usage:
        coinglass-mcp                          # stdio (Claude Desktop)
        coinglass-mcp --transport sse          # SSE on port 8000
        coinglass-mcp --transport sse --port 8100  # SSE on custom port
        coinglass-mcp --transport sse --auth-token your-token  # SSE with bearer auth
        coinglass-mcp --transport sse --host 0.0.0.0 --port 8100  # public
    """
    import argparse

    parser = argparse.ArgumentParser(description="CoinGlass MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind SSE server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE server (default: 8000)",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Optional bearer token for SSE. If set, clients must send Authorization: Bearer <token>.",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        auth_token = args.auth_token.strip() if isinstance(args.auth_token, str) else None
        if auth_token:
            mcp.auth = StaticBearerTokenVerifier(auth_token)
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
