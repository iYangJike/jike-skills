#!/usr/bin/env python3
"""一键查 A 股 / 美股实时价（基于 a-stock-data / global-stock-data 同源接口）

用法:
  /Users/a1/venv-quant/bin/python /Users/a1/stock-skills/bin/quote.py 600519
  /Users/a1/venv-quant/bin/python /Users/a1/stock-skills/bin/quote.py SNDK
  /Users/a1/venv-quant/bin/python /Users/a1/stock-skills/bin/quote.py 600519 SNDK AAPL 00700
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

import requests

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
)


def is_us_ticker(code: str) -> bool:
    c = code.strip()
    if re.fullmatch(r"\d{6}", c):
        return False
    if re.fullmatch(r"(sh|sz|bj)\d{6}", c, re.I):
        return False
    if re.fullmatch(r"\d{5}", c):  # 港股五位，这里仍走美股/港股分支
        return True
    return True


def a_share_prefix(code: str) -> str:
    c = code.strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith(("6", "5", "9")):
        return "sh" + c
    return "sz" + c


def quote_a_share(code: str) -> dict[str, Any]:
    q = a_share_prefix(code)
    r = SESSION.get(f"https://qt.gtimg.cn/q={q}", timeout=10)
    r.encoding = "gbk"
    text = r.text.strip()
    if '="' not in text:
        raise RuntimeError(f"A股无数据: {code}")
    body = text.split('="', 1)[1].rstrip('";')
    p = body.split("~")
    if len(p) < 45:
        raise RuntimeError(f"A股返回异常: {code} -> {text[:120]}")
    return {
        "market": "A",
        "name": p[1],
        "code": p[2],
        "price": _f(p[3]),
        "prev_close": _f(p[4]),
        "open": _f(p[5]),
        "volume": _i(p[6]),
        "high": _f(p[33]),
        "low": _f(p[34]),
        "change_pct": _f(p[32]),
        "time": p[30],
        "source": "tencent",
    }


def quote_us_sina(ticker: str) -> dict[str, Any]:
    url = f"https://hq.sinajs.cn/list=gb_{ticker.lower()}"
    r = SESSION.get(url, headers={"Referer": "https://finance.sina.com.cn/"}, timeout=10)
    r.encoding = "gbk"
    text = r.text.strip()
    m = re.search(r'="([^"]*)"', text)
    if not m or not m.group(1):
        raise RuntimeError(f"美股新浪无数据: {ticker}")
    p = m.group(1).split(",")
    # 新浪美股字段：名称,现价,涨跌幅,... 开高低收等
    return {
        "market": "US",
        "name": p[0],
        "code": ticker.upper(),
        "price": _f(p[1]),
        "change_pct": _f(p[2]),
        "time": p[3] if len(p) > 3 else "",
        "open": _f(p[5]) if len(p) > 5 else None,
        "high": _f(p[6]) if len(p) > 6 else None,
        "low": _f(p[7]) if len(p) > 7 else None,
        "prev_close": _f(p[26]) if len(p) > 26 else None,
        "volume": _i(p[10]) if len(p) > 10 else None,
        "source": "sina",
    }


def quote_us_tencent(ticker: str) -> dict[str, Any]:
    r = SESSION.get(f"https://qt.gtimg.cn/q=us{ticker.upper()}", timeout=10)
    r.encoding = "gbk"
    text = r.text.strip()
    if "pv_none_match" in text or '="' not in text:
        raise RuntimeError(f"美股腾讯无数据: {ticker}")
    body = text.split('="', 1)[1].rstrip('";')
    p = body.split("~")
    return {
        "market": "US",
        "name": p[1],
        "code": p[2],
        "price": _f(p[3]),
        "prev_close": _f(p[4]),
        "open": _f(p[5]),
        "volume": _i(p[6]),
        "high": _f(p[33]) if len(p) > 33 else None,
        "low": _f(p[34]) if len(p) > 34 else None,
        "change_pct": _f(p[32]) if len(p) > 32 else None,
        "time": p[30] if len(p) > 30 else "",
        "source": "tencent",
    }


def quote_one(code: str) -> dict[str, Any]:
    code = code.strip()
    if not code:
        raise ValueError("空代码")
    # 纯 6 位数字视为 A 股
    if re.fullmatch(r"(?:sh|sz|bj)?\d{6}", code, re.I):
        return quote_a_share(code)
    # 其余先试美股新浪，失败再腾讯
    try:
        return quote_us_sina(code)
    except Exception:
        return quote_us_tencent(code)


def _f(x: Any):
    try:
        if x in (None, "", "-"):
            return None
        return float(x)
    except Exception:
        return None


def _i(x: Any):
    v = _f(x)
    return int(v) if v is not None else None


def fmt(row: dict[str, Any]) -> str:
    chg = row.get("change_pct")
    chg_s = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "-"
    return (
        f"{row.get('market','?'):<2}  {row.get('code','?'):<10}  {row.get('name','?'):<10}  "
        f"价={row.get('price')}  涨跌={chg_s}  "
        f"开={row.get('open')} 高={row.get('high')} 低={row.get('low')}  "
        f"量={row.get('volume')}  时间={row.get('time')}  [{row.get('source')}]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一键查 A 股 / 美股实时价")
    parser.add_argument("codes", nargs="+", help="股票代码，如 600519 SNDK AAPL")
    args = parser.parse_args(argv)
    rc = 0
    for code in args.codes:
        try:
            print(fmt(quote_one(code)))
        except Exception as e:
            rc = 1
            print(f"ERR  {code}: {e}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
