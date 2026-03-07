#!/usr/bin/env python3
"""Closed-loop sweep runner for CoinGlass MCP tools."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

COINS = ["ETH", "BTC", "VIRTUAL", "XTZ"]
RESULTS_PATH = Path("/tmp/closed-loop-sweep-results.json")


@dataclass(frozen=True)
class ToolCase:
    tool: str
    once: bool
    build_params: Callable[[str], dict[str, str | int | float]]


def _etf_asset(coin: str) -> str:
    return "ethereum" if coin == "ETH" else "bitcoin"


TOOL_CASES: list[ToolCase] = [
    ToolCase("coinglass_article", False, lambda coin: {"action": "list", "language": "en", "per_page": 5}),
    ToolCase("coinglass_calendar", False, lambda coin: {"action": "economic_data", "language": "en"}),
    ToolCase("coinglass_market_info", False, lambda coin: {"action": "exchanges"}),
    ToolCase("coinglass_market_data", False, lambda coin: {"action": "pairs_summary", "symbol": coin}),
    ToolCase("coinglass_price_history", False, lambda coin: {"exchange": "Binance", "pair": f"{coin}USDT", "interval": "h1", "limit": 120}),
    ToolCase("coinglass_oi_history", False, lambda coin: {"action": "aggregated", "symbol": coin, "interval": "h4", "limit": 120}),
    ToolCase("coinglass_oi_distribution", False, lambda coin: {"action": "by_exchange", "symbol": coin}),
    ToolCase("coinglass_funding_history", False, lambda coin: {"action": "oi_weighted", "symbol": coin, "interval": "h8", "limit": 120}),
    ToolCase("coinglass_funding_current", False, lambda coin: {"action": "rates", "symbol": coin}),
    ToolCase("coinglass_long_short", False, lambda coin: {"action": "global", "exchange": "Binance", "pair": f"{coin}USDT", "interval": "h4", "limit": 120}),
    ToolCase("coinglass_liq_history", False, lambda coin: {"action": "aggregated", "symbol": coin, "interval": "h1", "limit": 120}),
    ToolCase("coinglass_liq_orders", False, lambda coin: {"exchange": "Binance", "symbol": coin}),
    ToolCase("coinglass_liq_heatmap", False, lambda coin: {"action": "coin_heatmap", "symbol": coin, "range": "7d", "model": 1}),
    ToolCase("coinglass_ob_history", False, lambda coin: {"action": "coin_depth", "symbol": coin, "interval": "h1", "limit": 120}),
    ToolCase("coinglass_ob_large_orders", False, lambda coin: {"action": "current", "exchange": "Binance", "pair": f"{coin}USDT"}),
    ToolCase("coinglass_whale_positions", False, lambda coin: {"action": "positions", "symbol": coin}),
    ToolCase("coinglass_bitfinex_longs_shorts", False, lambda coin: {"symbol": "BTC", "interval": "1d"}),
    ToolCase("coinglass_taker", False, lambda coin: {"action": "coin_history", "symbol": coin, "interval": "h1", "market": "futures", "limit": 120}),
    ToolCase("coinglass_spot", False, lambda coin: {"action": "coins_markets", "symbol": coin}),
    ToolCase("coinglass_options", False, lambda coin: {"action": "info", "symbol": coin}),
    ToolCase("coinglass_onchain", False, lambda coin: {"action": "balance_list", "symbol": coin}),
    ToolCase("coinglass_etf", False, lambda coin: {"action": "flows", "asset": _etf_asset(coin)}),
    ToolCase("coinglass_grayscale", False, lambda coin: {"action": "holdings"}),
    ToolCase("coinglass_indicators", False, lambda coin: {"action": "rsi", "symbol": coin}),
    ToolCase("coinglass_search", True, lambda coin: {"query": "funding"}),
    ToolCase("coinglass_config", True, lambda coin: {"action": "plan_features"}),
]


def known_skip(tool: str, coin: str) -> str | None:
    if tool == "coinglass_options" and coin in {"VIRTUAL", "XTZ"}:
        return "CoinGlass options supports BTC/ETH only"
    if tool == "coinglass_onchain" and coin in {"VIRTUAL", "XTZ"}:
        return "CoinGlass onchain balance_list returns 500 for VIRTUAL/XTZ"
    return None


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _looks_like_header_row(line: str) -> bool:
    if "|" not in line:
        return False
    cells = [c.strip() for c in line.split("|")]
    if not cells:
        return False
    return all(bool(re.fullmatch(r"[A-Za-z0-9_/% ()-]+", c)) for c in cells)


def has_signal(text: str) -> bool:
    lines = [ln.rstrip() for ln in text.splitlines()]
    if not lines:
        return False

    body = lines[1:] if len(lines) > 1 else []
    if not body:
        return False

    # Remove truncation marker and obvious empty lines.
    body = [ln.strip() for ln in body if ln.strip() and not ln.strip().startswith("... (")]
    if not body:
        return False

    # Skip first table header line.
    if body and _looks_like_header_row(body[0]):
        body = body[1:]
    if not body:
        return False

    for line in body:
        for token in re.split(r"[|,=]", line):
            tok = token.strip().strip('"')
            if not tok:
                continue

            low = tok.lower()
            if low in {"-", "--", "none", "null", "n/a", "na", "[]", "{}", "<list>", "<dict>"}:
                continue
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", tok):
                continue
            if re.fullmatch(r"\d{2}:\d{2}", tok):
                continue
            if re.fullmatch(r"[A-Za-z_]+", tok) and low in {
                "time", "date", "symbol", "exchange", "pair", "price", "volume", "ratio",
                "summary", "classification", "current", "fund", "ticker", "actions", "features",
            }:
                continue
            return True

    return False


def run_call(tool: str, params: dict[str, str | int | float]) -> tuple[bool, str, str]:
    selector = f"coinglass.{tool}"
    cmd = ["mcporter", "call", selector]
    for k, v in params.items():
        cmd.append(f"{k}={v}")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = strip_ansi((proc.stdout or "") + (proc.stderr or "")).strip()

    if not output:
        return False, "empty_output", output

    if "validation error for call[" in output:
        return False, "validation_error", output
    if output.startswith("Error calling tool"):
        return False, "tool_error", output
    if output.startswith("Unknown tool"):
        return False, "unknown_tool", output

    parsed = None
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        text = parsed.get("text")
        if not isinstance(text, str):
            return False, "missing_text", output
        if "No data returned" in text:
            return False, "no_data", output
        if has_signal(text):
            return True, "ok", output
        return False, "no_signal", output

    if has_signal(output):
        return True, "ok_plain", output

    return False, "unparsed_no_signal", output


def iter_runs(selected_tool: str | None, selected_coin: str | None):
    for case in TOOL_CASES:
        if selected_tool and case.tool != selected_tool:
            continue

        coins = [COINS[0]] if case.once else COINS
        for coin in coins:
            if selected_coin and coin != selected_coin:
                continue
            yield case, coin


def main() -> int:
    parser = argparse.ArgumentParser(description="Run closed-loop CoinGlass sweep")
    parser.add_argument("--tool", help="Filter by tool name (e.g., coinglass_liq_history)")
    parser.add_argument("--coin", help="Filter by coin symbol")
    parser.add_argument("--json", action="store_true", help="Print full JSON results")
    args = parser.parse_args()

    results: list[dict[str, str | bool | dict[str, str | int | float]]] = []

    for case, coin in iter_runs(args.tool, args.coin):
        skip_reason = known_skip(case.tool, coin)
        params = case.build_params(coin)

        if skip_reason:
            results.append(
                {
                    "tool": case.tool,
                    "coin": coin,
                    "status": "SKIP",
                    "reason": skip_reason,
                    "params": params,
                    "output": "",
                }
            )
            print(f"SKIP {case.tool:<32} {coin:<7} {skip_reason}")
            continue

        ok, reason, output = run_call(case.tool, params)
        status = "PASS" if ok else "FAIL"
        results.append(
            {
                "tool": case.tool,
                "coin": coin,
                "status": status,
                "reason": reason,
                "params": params,
                "output": output,
            }
        )
        print(f"{status} {case.tool:<32} {coin:<7} {reason}")

    total = len(results)
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    eligible = total - skipped

    summary = {
        "total": total,
        "eligible": eligible,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "score": f"{passed}/{eligible}",
    }

    payload = {"summary": summary, "results": results}
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("-" * 88)
    print(
        f"SUMMARY total={total} eligible={eligible} pass={passed} fail={failed} skip={skipped} score={passed}/{eligible}"
    )
    print(f"Saved: {RESULTS_PATH}")

    if args.json:
        print(json.dumps(payload, indent=2))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
