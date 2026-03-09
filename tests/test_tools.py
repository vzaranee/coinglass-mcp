"""Tests for CoinGlass MCP tools."""

import pytest
from unittest.mock import AsyncMock

from coinglass_mcp.server import (
    coinglass_market_info,
    coinglass_market_data,
    coinglass_price_history,
    coinglass_oi_history,
    coinglass_oi_distribution,
    coinglass_funding_history,
    coinglass_funding_current,
    coinglass_long_short,
    coinglass_liq_history,
    coinglass_liq_orders,
    coinglass_whale_positions,
    coinglass_taker,
    coinglass_indicators,
    coinglass_spot,
    coinglass_onchain,
    coinglass_ob_large_orders,
    coinglass_config,
    check_plan,
    check_interval,
    check_params,
)
from coinglass_mcp.client import CoinGlassClient


def get_fn(tool):
    """Extract function from FastMCP FunctionTool wrapper."""
    return tool.fn if hasattr(tool, "fn") else tool


def assert_text_result(result, *expected_substrings):
    """Assert tool response uses text format and contains expected content."""
    assert "text" in result
    text = result["text"]
    assert isinstance(text, str)
    for expected in expected_substrings:
        assert expected in text
    return text


class TestHelperFunctions:
    """Test helper functions."""

    def test_check_plan_passes_for_valid_plan(self, mock_context):
        """check_plan passes when plan is sufficient."""
        ctx = mock_context("standard")
        # Should not raise
        check_plan(ctx, "orders")

    def test_check_plan_fails_for_insufficient_plan(self, mock_context):
        """check_plan raises for insufficient plan."""
        ctx = mock_context("hobbyist")
        with pytest.raises(ValueError, match="requires standard plan"):
            check_plan(ctx, "orders")

    def test_check_interval_passes_for_valid_interval(self, mock_context):
        """check_interval passes for allowed interval."""
        ctx = mock_context("standard")
        # Should not raise
        check_interval(ctx, "m5")

    def test_check_interval_fails_for_restricted_interval(self, mock_context):
        """check_interval raises for restricted interval."""
        ctx = mock_context("hobbyist")
        with pytest.raises(ValueError, match="not available"):
            check_interval(ctx, "m5")

    def test_check_params_passes_with_required_params(self):
        """check_params passes when all required params present."""
        # Should not raise
        check_params("pair", exchange="Binance", pair="BTCUSDT")

    def test_check_params_fails_missing_params(self):
        """check_params raises for missing required params."""
        with pytest.raises(ValueError, match="requires parameters"):
            check_params("pair", exchange="Binance", pair=None)


class TestMarketTools:
    """Test market tools."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http, mock_response):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_market_info_coins(self, setup_context, mock_response):
        """coinglass_market_info returns coins list."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response(["BTC", "ETH", "SOL"])

        fn = get_fn(coinglass_market_info)
        result = await fn("coins", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_market_info(coins)",
            "BTC",
            "ETH",
            "SOL",
        )

    async def test_market_info_exchanges(self, setup_context, mock_response):
        """coinglass_market_info returns exchanges."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response({
            "Binance": ["BTCUSDT"],
            "OKX": ["BTCUSDT"],
        })

        fn = get_fn(coinglass_market_info)
        result = await fn("exchanges", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_market_info(exchanges)",
            "Binance",
            "OKX",
        )

    async def test_market_data_coins_summary(self, setup_context, mock_response):
        """coinglass_market_data returns coins summary for a symbol."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response({"symbol": "BTC", "price": 50000})

        fn = get_fn(coinglass_market_data)
        result = await fn("coins_summary", symbol="BTC", ctx=ctx)

        assert_text_result(result, "coinglass_market_data(coins_summary)", "BTC")

    async def test_market_data_coins_summary_requires_symbol(self, setup_context):
        """coinglass_market_data coins_summary requires symbol."""
        ctx, _ = setup_context()

        fn = get_fn(coinglass_market_data)
        with pytest.raises(ValueError, match="requires symbol"):
            await fn("coins_summary", ctx=ctx)

    async def test_market_data_pairs_summary(self, setup_context, mock_response):
        """coinglass_market_data returns pairs summary."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"symbol": "BTCUSDT", "price": 50000},
            {"symbol": "ETHUSDT", "price": 3000},
        ])

        fn = get_fn(coinglass_market_data)
        result = await fn("pairs_summary", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_market_data(pairs_summary)",
            "BTCUSDT",
            "ETHUSDT",
        )

    async def test_price_history(self, setup_context, mock_response):
        """coinglass_price_history returns OHLC data."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"t": 1700000000, "o": 50000, "h": 51000, "l": 49000, "c": 50500},
        ])

        fn = get_fn(coinglass_price_history)
        result = await fn(
            exchange="Binance",
            pair="BTCUSDT",
            interval="h1",
            ctx=ctx,
        )

        assert_text_result(
            result,
            "coinglass_price_history(price_history)",
            "50000.00",
        )

    async def test_price_history_interval_check(self, setup_context, mock_response):
        """coinglass_price_history checks interval restriction."""
        ctx, mock_http = setup_context("hobbyist")

        fn = get_fn(coinglass_price_history)
        with pytest.raises(ValueError, match="not available"):
            await fn(
                exchange="Binance",
                pair="BTCUSDT",
                interval="m5",  # Not available for hobbyist
                ctx=ctx,
            )


class TestDerivativesTools:
    """Test derivatives tools."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http, mock_response):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_oi_history_aggregated(self, setup_context, mock_response):
        """coinglass_oi_history returns aggregated OI."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"t": 1700000000, "o": 10000, "h": 11000, "l": 9000, "c": 10500},
        ])

        fn = get_fn(coinglass_oi_history)
        result = await fn(
            action="aggregated",
            symbol="BTC",
            ctx=ctx,
        )

        assert_text_result(result, "coinglass_oi_history(aggregated)", "10.50K")

    async def test_oi_history_pair_requires_params(self, setup_context):
        """coinglass_oi_history pair action requires exchange+pair."""
        ctx, _ = setup_context()

        fn = get_fn(coinglass_oi_history)
        with pytest.raises(ValueError, match="requires parameters"):
            await fn(action="pair", ctx=ctx)

    async def test_oi_distribution(self, setup_context, mock_response):
        """coinglass_oi_distribution returns exchange breakdown."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"exchange": "Binance", "oi": 5000000000},
            {"exchange": "OKX", "oi": 3000000000},
        ])

        fn = get_fn(coinglass_oi_distribution)
        result = await fn(
            action="by_exchange",
            symbol="BTC",
            ctx=ctx,
        )

        assert_text_result(
            result,
            "coinglass_oi_distribution(by_exchange)",
            "Binance",
            "OKX",
        )

    async def test_funding_history(self, setup_context, mock_response):
        """coinglass_funding_history returns funding OHLC."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"t": 1700000000, "o": 0.0001, "h": 0.0002, "l": 0.0001, "c": 0.00015},
        ])

        fn = get_fn(coinglass_funding_history)
        result = await fn(
            action="oi_weighted",
            symbol="BTC",
            ctx=ctx,
        )

        assert_text_result(result, "coinglass_funding_history(oi_weighted)", "0.01%")

    async def test_funding_current_rates(self, setup_context, mock_response):
        """coinglass_funding_current returns current rates."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"symbol": "BTC", "rate": 0.0001},
        ])

        fn = get_fn(coinglass_funding_current)
        result = await fn(action="rates", ctx=ctx)

        assert_text_result(result, "coinglass_funding_current(rates)", "BTC")

    async def test_funding_current_rates_filters_symbol_client_side(self, setup_context, mock_response):
        """coinglass_funding_current rates applies symbol filter after API response."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"symbol": "BTC", "rate": 0.0001},
            {"symbol": "ETH", "rate": 0.0002},
        ])

        fn = get_fn(coinglass_funding_current)
        result = await fn(action="rates", symbol="ETH", ctx=ctx)

        text = assert_text_result(result, "coinglass_funding_current(rates)", "ETH")
        assert "BTC" not in text

    async def test_funding_current_arbitrage_formats_nested_fields(self, setup_context, mock_response):
        """coinglass_funding_current arbitrage formats nested buy/sell exchange fields."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {
                "symbol": "SXP",
                "buy": {"exchange": "Bybit", "funding_rate": -4.0},
                "sell": {"exchange": "KuCoin", "funding_rate": -1.05},
                "apr": 3230.25,
                "spread": 3.11,
            },
        ])

        fn = get_fn(coinglass_funding_current)
        result = await fn(action="arbitrage", usd=10000, ctx=ctx)

        assert_text_result(
            result,
            "coinglass_funding_current(arbitrage)",
            "SXP",
            "Bybit",
            "KuCoin",
            "3.11",
            "3230.25",
        )

    async def test_long_short(self, setup_context, mock_response):
        """coinglass_long_short returns ratio data."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"t": 1700000000, "longRatio": 0.52, "shortRatio": 0.48},
        ])

        fn = get_fn(coinglass_long_short)
        result = await fn(
            action="global",
            exchange="Binance",
            pair="BTCUSDT",
            ctx=ctx,
        )

        assert_text_result(result, "coinglass_long_short(global)", "52.00%", "48.00%")


class TestLiquidationTools:
    """Test liquidation tools."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http, mock_response):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_liq_history(self, setup_context, mock_response):
        """coinglass_liq_history returns liquidation data."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"t": 1700000000, "longLiq": 1000000, "shortLiq": 500000},
        ])

        fn = get_fn(coinglass_liq_history)
        result = await fn(
            action="aggregated",
            symbol="BTC",
            ctx=ctx,
        )

        assert_text_result(result, "coinglass_liq_history(aggregated)", "1.00M")

    async def test_liq_history_max_pain_filters_symbol_client_side(self, setup_context, mock_response):
        """coinglass_liq_history max_pain applies symbol filter after API response."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {
                "symbol": "BTC",
                "long_max_pain_price": 51000,
                "long_max_pain_level": 1000000,
                "short_max_pain_price": 49000,
                "short_max_pain_level": 900000,
            },
            {
                "symbol": "ETH",
                "long_max_pain_price": 3500,
                "long_max_pain_level": 200000,
                "short_max_pain_price": 3300,
                "short_max_pain_level": 180000,
            },
        ])

        fn = get_fn(coinglass_liq_history)
        result = await fn(action="max_pain", symbol="ETH", range="24h", ctx=ctx)

        text = assert_text_result(result, "coinglass_liq_history(max_pain)", "ETH", "3500.00")
        assert "BTC" not in text

    async def test_liq_orders_requires_plan(self, setup_context):
        """coinglass_liq_orders requires standard+ plan."""
        ctx, _ = setup_context("hobbyist")

        fn = get_fn(coinglass_liq_orders)
        with pytest.raises(ValueError, match="requires standard plan"):
            await fn(ctx=ctx)

    async def test_liq_orders(self, setup_context, mock_response):
        """coinglass_liq_orders returns order stream."""
        ctx, mock_http = setup_context("standard")
        mock_http.get.return_value = mock_response([
            {"symbol": "BTC", "side": "long", "value": 100000},
        ])

        fn = get_fn(coinglass_liq_orders)
        result = await fn(ctx=ctx)

        assert_text_result(result, "coinglass_liq_orders(orders)", "price_range")


class TestWhaleTools:
    """Test whale tools."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http, mock_response):
        """Set up context with mock client."""

        def _setup(plan="startup"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_whale_positions_requires_plan(self, mock_context, mock_http):
        """coinglass_whale_positions requires startup+ plan."""
        ctx = mock_context("hobbyist")
        client = CoinGlassClient(http=mock_http, api_key="test")
        ctx.request_context.lifespan_context["client"] = client

        fn = get_fn(coinglass_whale_positions)
        with pytest.raises(ValueError, match="requires startup plan"):
            await fn(action="positions", ctx=ctx)

    async def test_whale_positions(self, setup_context, mock_response):
        """coinglass_whale_positions returns positions."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"user": "0x123", "symbol": "BTC", "size": 1000000},
        ])

        fn = get_fn(coinglass_whale_positions)
        result = await fn(action="positions", ctx=ctx)

        assert_text_result(result, "coinglass_whale_positions(positions)", "0x123", "BTC")


class TestTakerTools:
    """Test taker tools."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http, mock_response):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_taker_coin_history(self, setup_context, mock_response):
        """coinglass_taker returns taker volume."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"t": 1700000000, "buyVol": 100, "sellVol": 80},
        ])

        fn = get_fn(coinglass_taker)
        result = await fn(
            action="coin_history",
            symbol="BTC",
            ctx=ctx,
        )

        assert_text_result(result, "coinglass_taker(coin_history)", "55.56%")

    async def test_taker_aggregated_ratio_uses_buy_sell_volumes(self, setup_context, mock_response):
        """coinglass_taker aggregated_ratio derives ratio from buy/sell volumes."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {
                "time": 1700000000000,
                "aggregated_buy_volume_usd": 100.0,
                "aggregated_sell_volume_usd": 50.0,
            },
        ])

        fn = get_fn(coinglass_taker)
        result = await fn(action="aggregated_ratio", symbol="BTC", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_taker(aggregated_ratio)",
            "100.00",
            "50.00",
            "0.67",
        )


class TestIndicatorTools:
    """Test indicator tools."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http, mock_response):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_fear_greed(self, setup_context, mock_response):
        """coinglass_indicators returns fear & greed."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"date": "2024-01-01", "value": 75, "classification": "Greed"},
        ])

        fn = get_fn(coinglass_indicators)
        result = await fn(action="fear_greed", ctx=ctx)

        assert_text_result(result, "coinglass_indicators(fear_greed)", "Greed", "75.00")

    async def test_rsi(self, setup_context, mock_response):
        """coinglass_indicators returns RSI data."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {"symbol": "BTC", "rsi": 65.5},
        ])

        fn = get_fn(coinglass_indicators)
        result = await fn(action="rsi", ctx=ctx)

        assert_text_result(result, "coinglass_indicators(rsi)", "BTC", "65.50")

    async def test_ahr999_uses_real_field_names(self, setup_context, mock_response):
        """coinglass_indicators ahr999 uses date_string/value/current/average fields."""
        ctx, mock_http = setup_context()
        mock_http.get.return_value = mock_response([
            {
                "date_string": "2011/02/01",
                "average_price": 0.1365,
                "ahr999_value": 4.44,
                "current_value": 0.626,
            },
        ])

        fn = get_fn(coinglass_indicators)
        result = await fn(action="ahr999", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_indicators(ahr999)",
            "2011/02/01",
            "4.44",
            "0.63",
            "0.14",
        )


class TestMetaTools:
    """Test meta tools."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http, mock_response):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_config_exchanges(self, setup_context, mock_response):
        """coinglass_config returns exchange list."""
        ctx, mock_http = setup_context()
        mock_http.get.side_effect = [
            mock_response(["Binance", "OKX"]),
            mock_response(["Binance", "Coinbase"]),
        ]

        fn = get_fn(coinglass_config)
        result = await fn(action="exchanges", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_config(exchanges)",
            '"futures"',
            "Binance",
        )

    async def test_config_intervals(self, setup_context):
        """coinglass_config returns intervals for plan."""
        ctx, _ = setup_context("hobbyist")

        fn = get_fn(coinglass_config)
        result = await fn(action="intervals", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_config(intervals)",
            '"your_plan": [',
            '"h12"',
            '"w1"',
        )

    async def test_config_plan_features(self, setup_context):
        """coinglass_config returns plan features."""
        ctx, _ = setup_context("standard")

        fn = get_fn(coinglass_config)
        result = await fn(action="plan_features", ctx=ctx)

        assert_text_result(
            result,
            "coinglass_config(plan_features)",
            '"plan": "standard"',
            "liq_orders",
        )


class TestPreviewMetadata:
    """Test preview/truncation metadata behavior."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_price_history_reports_preview_metadata(self, setup_context, mock_response):
        """History tools should report shown vs total rows in text + metadata."""
        ctx, mock_http = setup_context()
        rows = [
            {"t": 1700000000 + i * 3600, "o": 50000 + i, "h": 50010 + i, "l": 49990 + i, "c": 50005 + i}
            for i in range(50)
        ]
        mock_http.get.return_value = mock_response(rows)

        fn = get_fn(coinglass_price_history)
        result = await fn(exchange="Binance", pair="BTCUSDT", interval="h1", limit=50, ctx=ctx)

        text = assert_text_result(result, "coinglass_price_history(price_history)", "preview: showing")
        assert "of 50 rows" in text
        metadata = result["metadata"]
        assert metadata["requested_limit"] == 50
        assert metadata["total_rows"] == 50
        assert metadata["shown_rows"] == 24
        assert metadata["truncated"] is True
        assert "row_preview_limit" in str(metadata["truncation_reason"])

    async def test_large_orders_text_cap_is_disclosed(self, setup_context, mock_response):
        """If text hits output cap, response should disclose readable cap reason."""
        ctx, mock_http = setup_context()
        supported_pairs = {
            "Binance": [{"instrument_id": "BTC-USDT"}],
        }
        huge_time = "X" * 1200
        rows = [
            {
                "start_time": f"{huge_time}{i}",
                "limit_price": 60000 + i,
                "start_usd_value": 1_000_000 - i * 1000,
                "order_side": 2 if i % 2 == 0 else 1,
            }
            for i in range(20)
        ]
        mock_http.get.side_effect = [mock_response(supported_pairs), mock_response(rows)]

        fn = get_fn(coinglass_ob_large_orders)
        result = await fn(
            action="current",
            exchange="Binance",
            symbol="BTCUSDT",
            limit=200,
            ctx=ctx,
        )

        text = assert_text_result(result, "coinglass_ob_large_orders(current)", "text capped at 4000 chars")
        metadata = result["metadata"]
        assert metadata["requested_limit"] == 200
        assert metadata["truncated"] is True
        assert "max_output_chars" in str(metadata["truncation_reason"])


class TestActionValidationAndSpotFiltering:
    """Test action-specific validation and spot pairs filter behavior."""

    @pytest.fixture
    def setup_context(self, mock_context, mock_http):
        """Set up context with mock client."""

        def _setup(plan="standard"):
            ctx = mock_context(plan)
            client = CoinGlassClient(http=mock_http, api_key="test")
            ctx.request_context.lifespan_context["client"] = client
            return ctx, mock_http

        return _setup

    async def test_ob_large_orders_current_requires_symbol_or_pair(self, setup_context):
        """coinglass_ob_large_orders current must require symbol/pair."""
        ctx, _ = setup_context()
        fn = get_fn(coinglass_ob_large_orders)
        with pytest.raises(ValueError, match="requires symbol or pair"):
            await fn(action="current", exchange="Binance", ctx=ctx)

    async def test_onchain_assets_requires_exchange(self, setup_context):
        """coinglass_onchain assets must require exchange."""
        ctx, _ = setup_context()
        fn = get_fn(coinglass_onchain)
        with pytest.raises(ValueError, match="requires exchange"):
            await fn(action="assets", ctx=ctx)

    async def test_spot_pairs_applies_exchange_and_symbol_filters(self, setup_context, mock_response):
        """coinglass_spot pairs should honor exchange/symbol filters client-side."""
        ctx, mock_http = setup_context()
        payload = {
            "Binance": [
                {"base_asset": "BTC", "instrument_id": "BTCUSDT", "exchange": "Binance"},
                {"base_asset": "ETH", "instrument_id": "ETHUSDT", "exchange": "Binance"},
            ],
            "OKX": [
                {"base_asset": "BTC", "instrument_id": "BTC-USDT", "exchange": "OKX"},
            ],
        }

        def _mock_get(_url, *, params=None, **_kwargs):
            if params and params.get("exchange"):
                return mock_response([])
            return mock_response(payload)

        mock_http.get.side_effect = _mock_get

        fn = get_fn(coinglass_spot)
        result = await fn(action="pairs", exchange="Binance", symbol="BTC", ctx=ctx)

        text = assert_text_result(result, "coinglass_spot(pairs)", "BTCUSDT")
        assert "ETHUSDT" not in text
        assert "OKX" not in text
        metadata = result["metadata"]
        assert "exchange=Binance" in metadata["filters_applied"]
        assert "symbol=BTC" in metadata["filters_applied"]
        assert mock_http.get.call_args.kwargs.get("params") is None

    async def test_spot_pairs_exchange_filter_not_forwarded_upstream(
        self, setup_context, mock_response
    ):
        """coinglass_spot pairs exchange-only filter must run client-side."""
        ctx, mock_http = setup_context()
        payload = {
            "Binance": [
                {"base_asset": "BTC", "instrument_id": "BTCUSDT", "exchange": "Binance"},
            ],
            "Coinbase": [
                {"base_asset": "BTC", "instrument_id": "BTC-USD", "exchange": "Coinbase"},
            ],
        }

        def _mock_get(_url, *, params=None, **_kwargs):
            if params and params.get("exchange"):
                return mock_response([])
            return mock_response(payload)

        mock_http.get.side_effect = _mock_get

        fn = get_fn(coinglass_spot)
        result = await fn(action="pairs", exchange="Coinbase", ctx=ctx)

        text = assert_text_result(result, "coinglass_spot(pairs)", "BTC-USD")
        assert "Binance" not in text
        metadata = result["metadata"]
        assert "exchange=Coinbase" in metadata["filters_applied"]
        assert mock_http.get.call_args.kwargs.get("params") is None

    async def test_spot_pairs_symbol_filter_avoids_prefix_false_positives(self, setup_context, mock_response):
        """Base-symbol filter should not match prefix variants like ETHW for ETH."""
        ctx, mock_http = setup_context()
        payload = [
            {"base_asset": "ETH", "instrument_id": "ETHUSDT", "exchange": "Binance"},
            {"base_asset": "ETHW", "instrument_id": "ETHWUSDT", "exchange": "Binance"},
        ]
        mock_http.get.return_value = mock_response(payload)

        fn = get_fn(coinglass_spot)
        result = await fn(action="pairs", symbol="ETH", ctx=ctx)

        text = assert_text_result(result, "coinglass_spot(pairs)", "ETHUSDT")
        assert "ETHWUSDT" not in text
        metadata = result["metadata"]
        assert "symbol=ETH" in metadata["filters_applied"]

    async def test_spot_pairs_list_payload_symbol_filter(self, setup_context, mock_response):
        """List payload variants should still apply strict base-symbol filtering."""
        ctx, mock_http = setup_context()
        payload = [
            {"exchange_name": "Binance", "symbol": "ETH-USDT"},
            {"exchange_name": "Binance", "pair": "ETHW-USDT"},
            {"exchange_name": "Binance", "instrument_id": "BTCUSDT"},
        ]
        mock_http.get.return_value = mock_response(payload)

        fn = get_fn(coinglass_spot)
        result = await fn(action="pairs", symbol="ETH", ctx=ctx)

        text = assert_text_result(result, "coinglass_spot(pairs)", "ETH-USDT")
        assert "ETHW-USDT" not in text
        assert "BTCUSDT" not in text
