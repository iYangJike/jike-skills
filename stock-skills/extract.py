"""从 a-stock-data SKILL.md 抽取 python 代码块 → 逐块 AST 过滤（去掉示例调用）→ 生成可导入模块。"""
import ast

SRC = "/Users/a1/stock-skills/a-stock-data/SKILL.md"
lines = open(SRC, encoding="utf-8").read().split("\n")

SECTIONS = [("helpers", 461, 1054), ("layer1", 1055, 1916),
            ("layer3", 2307, 3011), ("layer4", 3012, 3490)]

def fences(lo, hi):
    """返回区间内所有 ```python 围栏内容（逐块，围栏天然闭合）。"""
    blocks, cur = [], None
    for i in range(lo - 1, min(hi, len(lines))):
        ln = lines[i]
        if ln.startswith("```"):
            if cur is None:
                cur = [] if ln[3:].strip().lower() == "python" else False
            elif cur is not False:
                blocks.append("\n".join(cur)); cur = None
            else:
                cur = None
            continue
        if isinstance(cur, list):
            cur.append(ln)
    if isinstance(cur, list):
        blocks.append("\n".join(cur))
    return blocks

KEEP = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
        ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Try, ast.If)

preamble = '''# -*- coding: utf-8 -*-
# 自动抽取自 a-stock-data SKILL.md v3.10.3（仅去注释与示例调用，代码逻辑原样保留）
import os, re, json, time, random, socket, hashlib, base64, io, csv, math
import datetime as dt
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple, Iterable
import requests
import pandas as pd
import numpy as np
'''

body, dropped, bad = [], [], []
for name, lo, hi in SECTIONS:
    kept_src = []
    for blk in fences(lo, hi):
        try:
            tree = ast.parse(blk)
        except SyntaxError as e:
            bad.append((name, str(e).split("(")[0], blk.splitlines()[0][:60]))
            continue
        keep = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                keep.append(node); continue
            if isinstance(node, ast.Expr):
                dropped.append(ast.unparse(node).splitlines()[0][:70]); continue
            if isinstance(node, KEEP):
                keep.append(node)
        if keep:
            t = ast.Module(body=keep, type_ignores=[])
            kept_src.append(ast.unparse(t))
    body.append(f"# ============ {name} ============\n" + "\n".join(kept_src))

out = preamble + "\n\n".join(body) + "\n"
open("/Users/a1/stock-skills/astock_ext.py", "w", encoding="utf-8").write(out)

funcs = [n.name for n in ast.parse(out).body if isinstance(n, ast.FunctionDef)]
print(f"生成 {len(out.splitlines())} 行，顶层函数 {len(funcs)} 个；丢弃示例语句 {len(dropped)}；语法失败块 {len(bad)}")
for b in bad[:8]:
    print("  skip:", b)
print("丢弃示例:", dropped[:10])
need = ["tencent_quote", "tencent_kline", "norm_ticker", "get_prefix", "em_get", "_v39_http",
        "eastmoney_concept_blocks", "eastmoney_fund_flow_minute", "margin_trading",
        "stock_fund_flow_120d", "chip_distribution", "block_trade", "holder_num_change",
        "dividend_history", "dragon_tiger_board", "industry_comparison"]
print("关键函数缺失:", [n for n in need if n not in funcs])