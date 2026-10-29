# -*- coding: utf-8 -*-
# 自动抽取自 a-stock-data SKILL.md v3.10.3（仅去注释与示例调用，代码逻辑原样保留）
import os, re, json, time, random, socket, hashlib, base64, io, csv, math
import datetime as dt
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple, Iterable
import requests
import pandas as pd
import numpy as np
# ============ helpers ============
import socket
from mootdx.quotes import Quotes
_TDX_SERVERS = [('119.97.185.59', 7709), ('124.70.133.119', 7709), ('116.205.183.150', 7709), ('123.60.73.44', 7709), ('116.205.163.254', 7709), ('121.36.225.169', 7709), ('123.60.70.228', 7709), ('124.71.9.153', 7709), ('110.41.147.114', 7709), ('124.71.187.122', 7709)]

def _probe(ip, port, timeout=2.0):
    """TCP 握手探测（快速粗筛）。注意：握手成功 ≠ 能取数，必须再经 _validate 验活。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False

def _validate(client, market: str='std', check: str='bars') -> bool:
    """真实取数验活：坏服务器可 TCP 握手通过却回 2 字节空 body → 静默空表。用一次真实请求兜底。

    check='bars'   ：用 K 线请求验活（§1.7 行情类调用）；
    check='finance'：财务快照、F10 类别表里的「最新提示」及其正文都要取到才算活（§6.1 财务 / §6.2、§7.2 F10 调用），
                     只验财务会选中「财务正常、F10 为空」的服务器，F10 随后静默给空文本；
                     只看类别表非空，又会放过只回别的类别或畸形对象的服务器。
    两者要分开：2026-09 起通达信公开服务器的行情类命令（bars / quotes / transaction）普遍返回空表，
    而 finance / F10「最新提示」/ 除权除息仍正常（#52）。财务类调用若仍用 K 线验活，会被误判成「全部不可达」。
    验活样本 '000001' 是 A 股代码，只对 market='std' 有意义。其它市场（如扩展行情 'ext'）
    用它必然取不到数，会把所有正常服务器都判死、误报「全部不可达」，故非 std 时跳过验活。
    """
    if market != 'std':
        return True
    try:
        if check == 'finance':
            cats = client.F10C(symbol='000001')
            if not any((isinstance(c, dict) and c.get('name') == '最新提示' for c in cats)):
                return False
            text = client.F10(symbol='000001', name='最新提示')
            if not isinstance(text, str) or not text.strip():
                return False
            df = client.finance(symbol='000001')
        else:
            df = client.bars(symbol='000001', frequency=9, offset=1)
        return df is not None and (not df.empty)
    except Exception:
        return False

def tdx_client(market='std', check='bars'):
    """
    创建 mootdx 客户端，规避 0.11.x BESTIP.HQ 空串 bug + 坏服务器静默空表（#43）。
    check: 'bars'（默认，K 线 / 盘口 / 逐笔）或 'finance'（财务快照 / F10），决定用哪类请求验活（#52）。
    每个候选都必须「真实取数验活」通过才采用（_probe TCP 握手是假阳性来源）：
      1) 顺序探测 _TDX_SERVERS，对 probe 通过者再 _validate 真实取数，取第一个验活成功的；
      2) 全部失败 → 回退 mootdx 自带 bestip 测速选优（同样验活）；
      3) 再回退裸 factory（老用户 config 已有可用 BESTIP 时成立）；
      4) 仍失败 → 抛 RuntimeError，明确报错而非静默返回空表 / 崩溃。
    """
    if check not in ('bars', 'finance'):
        raise ValueError("check 只能是 'bars' 或 'finance'")
    for ip, port in _TDX_SERVERS:
        if not _probe(ip, port):
            continue
        try:
            c = Quotes.factory(market=market, server=(ip, port))
            if _validate(c, market, check):
                return c
        except Exception:
            continue
    for kwargs in ({'bestip': True}, {}):
        try:
            c = Quotes.factory(market=market, **kwargs)
            if _validate(c, market, check):
                return c
        except Exception:
            continue
    hint = '海外网络通常全部超时（TCP 7709），请走国内代理或更新 _TDX_SERVERS 列表。'
    if check == 'bars':
        hint += "若国内网络也如此：2026-09 起通达信公开服务器的 K 线 / 盘口 / 逐笔命令普遍返回空（#52），K 线改用 §1.2 tencent_kline()（日周月 + 1~60 分钟）或 §1.3 tdx_daily_package()（全市场当日日线含成交额），逐笔改用 §1.4 tencent_ticks()（当日）；财务与 F10 用 tdx_client(check='finance') 仍可取。"
    raise RuntimeError('所有 mootdx 服务器均无法取到数据（TCP 可达但返回空 / 被 reset）。' + hint)
SH_INDEX = {'000300', '000905', '000016', '000688', '000852', '000010'}

def get_prefix(code: str) -> str:
    """6位代码 → 市场前缀（sh/sz/bj）。支持显式前缀/后缀（sh000016 / 000016.SH）透传以解决歧义。"""
    c = code.lower().strip()
    if c.endswith(('.sh', '.sz', '.bj')):
        return c[-2:]
    if c.endswith(('.xshg', '.xshe')):
        return 'sh' if c.endswith('.xshg') else 'sz'
    if c.startswith(('sh', 'sz', 'bj')):
        return c[:2]
    if c.startswith('92'):
        return 'bj'
    if c.startswith(('5', '6', '9')):
        return 'sh'
    if c.startswith(('4', '8')):
        return 'bj'
    if c in SH_INDEX:
        return 'sh'
    return 'sz'
import re
_TICKER_RE = re.compile('^(?:(sh|sz|bj)(\\d{6})|(\\d{6})(?:\\.(sh|sz|bj|xshg|xshe))?)$', re.IGNORECASE)
_JQ_SUFFIX = {'xshg': 'sh', 'xshe': 'sz'}

def _natural_market(digits: str) -> str:
    """6 位码的自然归属市场。仅用于校验显式前缀是否自相矛盾。
    注意 000xxx 是沪指数/深个股共用的歧义段，由调用处单独处理，不走这里。"""
    if digits.startswith(('4', '8', '92')):
        return 'bj'
    if digits[0] in ('5', '6', '9'):
        return 'sh'
    return 'sz'

def norm_ticker(code: str, stock_only: bool=False) -> str:
    """任意受支持写法 → 纯 6 位数字代码。

    支持 600519 / SH600519 / sh600519 / 600519.SH / BJ920982 等。
    stock_only=True：个股专用接口（研报、一致预期等）传这个，会拒绝显式指数写法。
    ⚠️ 不匹配时**抛 ValueError，绝不静默返回空串或猜一个代码**——
    否则调用方会把「代码格式写错」误读成「这只票没有数据」，
    或者更糟：拿到另一只股票的数据还以为是对的。
    """
    raw = str(code).strip()
    m = _TICKER_RE.match(raw)
    if not m:
        raise ValueError(f'无法把 {code!r} 解析为 6 位股票代码；支持格式：600519 / SH600519 / sh600519 / 600519.SH / 600519.XSHG（聚宽）（前缀与后缀二选一，不能同时写）')
    digits = m.group(2) or m.group(3)
    market = (m.group(1) or m.group(4) or '').lower()
    market = _JQ_SUFFIX.get(market, market)
    if market:
        if digits.startswith('000'):
            if market == 'bj':
                raise ValueError(f'{code!r} 市场标识与号段矛盾：000xxx 不属北交所。')
            if stock_only and market == 'sh':
                raise ValueError(f'{code!r} 指向沪市指数而非个股（沪市无 000xxx 个股），本接口只服务个股。要查同号段的深市个股请显式传 sz{digits}。')
        else:
            nat = _natural_market(digits)
            if market != nat:
                raise ValueError(f'{code!r} 的市场标识与号段矛盾：{digits} 属 {nat} 市，而不是 {market} 市。（改用 {nat}{digits} 或去掉市场标识）')
    return digits

def em_market_code(code: str) -> int:
    """东财 secid 的市场号：**沪=1，深/北=0**（V3.7.0 新增 · #46）。

    ⚠️ 绝不要用 `code.startswith("6")` 判市场 —— 那会把**沪市 ETF（51x）**、
    **科创板 ETF（588x）**、**沪 B 股（900x）** 全部错判成深市，接口返回 `data: null`。
    2026-08-19 实测：510300 / 588000 / 600519 / 688112 / 900901 → m=1；
    300750 / 159915 / 920982 / 832982 → m=0（北交所与深市共用 m=0）。
    """
    return 1 if get_prefix(code) == 'sh' else 0

def em_secid(code: str) -> str:
    """东财 push2/push2his 的 secid，如 `1.600519` / `0.300750`。"""
    return f'{em_market_code(code)}.{norm_ticker(code)}'

def to_joinquant(code: str) -> str:
    """任意受支持写法 → 聚宽代码，如 `600519.XSHG` / `000001.XSHE`（#55）。

    000 段歧义码按 get_prefix() 的规则走：`to_joinquant("sh000001")` → `000001.XSHG`（上证指数），
    `to_joinquant("000001")` → `000001.XSHE`（平安银行）。反方向（聚宽 → 本 Skill）用
    `get_prefix(c) + norm_ticker(c)` 得到 `sh000001` 这类显式前缀写法，再传给各端点。
    北交所：聚宽公开文档与 jqdatasdk 源码（2026-09-20 查）只有 .XSHG / .XSHE，没查到北交所后缀，抛 ValueError 不猜。
    """
    digits, market = (norm_ticker(code), get_prefix(code))
    if market == 'bj':
        raise ValueError(f'{code!r} 是北交所证券；聚宽公开文档未给出北交所后缀，不做转换')
    return f"{digits}.{('XSHG' if market == 'sh' else 'XSHE')}"
import time
import random
import requests
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
DATACENTER_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
EM_SESSION = requests.Session()
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _em_adapter = HTTPAdapter(max_retries=Retry(total=3, connect=3, backoff_factor=0.6, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET']))
    EM_SESSION.mount('https://', _em_adapter)
    EM_SESSION.mount('http://', _em_adapter)
except Exception:
    pass
from typing import Optional
EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]

def em_get(url: str, params: Optional[dict]=None, headers: Optional[dict]=None, timeout: int=15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA。
    所有 eastmoney.com 接口都应通过它请求，避免高频被封 IP。"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()

def eastmoney_datacenter(report_name: str, columns: str='ALL', filter_str: str='', page_size: int=50, sort_columns: str='', sort_types: str='-1') -> list[dict]:
    """东财数据中心统一查询 — 龙虎榜/解禁/融资融券/大宗交易/股东户数/分红 共用（已内置限流）"""
    params = {'reportName': report_name, 'columns': columns, 'filter': filter_str, 'pageNumber': '1', 'pageSize': str(page_size), 'sortColumns': sort_columns, 'sortTypes': sort_types, 'source': 'WEB', 'client': 'WEB'}
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get('result') and d['result'].get('data'):
        return d['result']['data']
    return []
import functools
import math
import re
from datetime import date as _date_cls, datetime, timezone
import pandas as pd
import requests
V39_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
V39_RETRY_SESSION = requests.Session()
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _v39_retry_adapter = HTTPAdapter(max_retries=Retry(total=3, connect=3, backoff_factor=0.6, respect_retry_after_header=False, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET']))
    V39_RETRY_SESSION.mount('https://', _v39_retry_adapter)
    V39_RETRY_SESSION.mount('http://', _v39_retry_adapter)
except Exception:
    V39_RETRY_SESSION = None

def _v39_http(url, params=None, data=None, headers=None, method='GET', timeout=(10, 40), allow_status=(), allow_redirects=True, session=None):
    """非东财的 HTTP 请求：带浏览器 UA。网络错误、非 2xx 一律抛 RuntimeError（不把错误页当数据）；
    allow_status 里的状态码（源用 404 表示「当天没发布」时）原样返回，由调用方判断。
    session：传 V39_RETRY_SESSION 复用连接并自动重试（哪些情况重试见它的定义）；不传每次新建连接、不重试。"""
    merged = {'User-Agent': V39_UA}
    merged.update(headers or {})
    try:
        response = (session or requests).request(method, url, params=params, data=data, headers=merged, timeout=timeout, allow_redirects=allow_redirects)
        if response.status_code not in allow_status:
            response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f'请求 {url} 失败: {type(exc).__name__}: {exc}') from exc
    return response

def _v39_json(response):
    """解析 JSON；不是 JSON 抛 RuntimeError。json 的解析错误是 ValueError 的子类，
    不转换会被调用方当成「确实没有数据」。"""
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{getattr(response, 'url', '')} 返回的不是 JSON，可能是错误页") from exc

def _v39_date(value):
    """'2026-09-18' / '20260918' / date 对象 → '2026-09-18'；其他写法抛 ValueError。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, _date_cls):
        return value.isoformat()
    text = str(value).strip()
    fmt = '%Y%m%d' if re.fullmatch('[0-9]{8}', text) else '%Y-%m-%d'
    return datetime.strptime(text, fmt).date().isoformat()

def _v39_src_date(value):
    """来源返回的日期 → 'YYYY-MM-DD'；认不出抛 RuntimeError（源格式变了，不是参数写错）。"""
    try:
        return _v39_date(value)
    except ValueError as exc:
        raise RuntimeError(f'来源返回了无法识别的日期 {value!r}') from exc

def _v39_num(value):
    """'1,234.50' → 1234.5；空串 / '-' / '--' / None → None；其他非数字抛 RuntimeError
    （来源给了认不出的值是「源的格式变了」，不能和参数错误的 ValueError 混在一起）。
    JSON 布尔值同样抛错：float(True)=1.0 会把格式错误静默写成价格 / 成交量。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise RuntimeError(f'来源在数值字段给了布尔值 {value!r}')
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (not math.isfinite(value)):
            return None
        return float(value)
    text = str(value).replace(',', '').strip()
    if text in ('', '-', '--', 'None', 'null'):
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise RuntimeError(f'来源返回了无法识别的数值 {value!r}') from exc
    return number if math.isfinite(number) else None

def _v39_rows(value, what):
    """来源里可能整段缺失的行列表：字段没有（None）按空处理，其余必须是对象列表。
    写成 `value or []` 会把 {} / '' / 0 这类结构改变也当成空表，静默丢掉整段数据。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all((isinstance(row, dict) for row in value)):
        raise RuntimeError(f'{what} 应为对象列表，实际是 {type(value).__name__}: {str(value)[:120]}')
    return value

def _v39_labels(value, what):
    """来源的标签数组（频道名之类）：None 按空处理，其余必须是字符串列表。
    直接 `value or []` 再 join，来源把数组改成字符串时会被拆成单字（'ab' → 'a,b'）。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all((isinstance(x, str) for x in value)):
        raise RuntimeError(f'{what} 应为字符串列表，实际是 {type(value).__name__}: {str(value)[:120]}')
    return value

def _v39_req_num(value, what):
    """必填数值（价格、成交量）：在 _v39_num 之上，空值 / NaN / inf 也抛 RuntimeError，不能当缺失放过。"""
    number = _v39_num(value)
    if number is None:
        raise RuntimeError(f'来源的 {what} 为空或不是有限数值: {value!r}')
    return number

def _v39_contract(func):
    """统一异常契约：来源行缺字段时 row["X"] 会漏出 KeyError，调用方按「参数错 / 没数据」处理就会
    把「来源格式变了」当成正常情况。这里把它转成带函数名和字段名的 RuntimeError。
    （函数内所有按用户参数取字典的地方都先校验过参数，不会走到这里。）"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyError as exc:
            raise RuntimeError(f'{func.__name__}: 来源数据缺少字段 {exc}，格式可能已变') from exc
    return wrapper

def _v39_count(value, what):
    """来源自报的页数 / 条数 → 非负 int。只认 int 或纯数字串；bool 抛错（int(True)=1 会让
    「只返回 1 条」通过完整性核对），其他写法也抛 RuntimeError。"""
    text = str(value).strip() if isinstance(value, (int, str)) and (not isinstance(value, bool)) else ''
    if not re.fullmatch('[0-9]+', text):
        raise RuntimeError(f'{what} 不是非负整数: {value!r}')
    return int(text)

def _v39_frame(rows, source, url, columns=None):
    """统一出表：附 source / source_url / fetched_at。rows 为空时返回带列名的空表，
    是否允许为空由调用方判断（「确实没有」与「接口坏了」要分开处理）。"""
    frame = pd.DataFrame(rows, columns=columns)
    frame['source'] = source
    frame['source_url'] = url
    frame['fetched_at'] = datetime.now(timezone.utc).isoformat()
    return frame

def _em_datacenter_strict(report_name, filter_str='', sort_columns='', sort_types='', page_size=500, max_rows=5000, columns='ALL', extra=None):
    """东财 datacenter 严格版：code=0 取数据；第 1 页就 9201(返回数据为空) → []；其他错误码直接抛。

    与旧 eastmoney_datacenter() 的区别：后者把任何失败都变成 []，调用方分不清
    「这只票确实没有」和「参数写错/被风控」。sortTypes 个数必须与 sortColumns 一致，
    否则东财返回 9501「排序字段和顺序数量不一致」。
    翻页中途失败（第 2 页起 9201、空页、非末页不满页、缺 pages / count、总页数或总条数变了、
    最终条数与 count 不符）抛 RuntimeError，不把部分结果当完整结果返回。
    payload / result 不是对象、data 不是由对象组成的列表，同样抛 RuntimeError。
    只有「第 1 页、pages=1、data 为空」才算确实没有数据。
    max_rows 只在来源自报总数 count > max_rows 时提前截断；count 不超过上限的，一律走完分页并核对总数。
    """
    n_cols = len([c for c in sort_columns.split(',') if c]) if sort_columns else 0
    n_types = len([t for t in sort_types.split(',') if t]) if sort_types else 0
    if n_cols != n_types:
        raise ValueError(f'sortColumns({n_cols}) 与 sortTypes({n_types}) 个数不一致')
    rows, page, first = ([], 1, None)
    while True:
        params = {'reportName': report_name, 'columns': columns, 'filter': filter_str, 'pageNumber': str(page), 'pageSize': str(page_size), 'sortColumns': sort_columns, 'sortTypes': sort_types, 'source': 'WEB', 'client': 'WEB'}
        params.update(extra or {})
        try:
            response = em_get(DATACENTER_URL, params=params, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f'东财 {report_name} 请求失败: {type(exc).__name__}: {exc}') from exc
        payload = _v39_json(response)
        if not isinstance(payload, dict):
            raise RuntimeError(f'东财 {report_name} 返回的不是 JSON 对象: {str(payload)[:100]}')
        if payload.get('code') == 9201:
            if page == 1:
                return []
            raise RuntimeError(f'东财 {report_name} 第 {page} 页返回「数据为空」，前面已取 {len(rows)} 条，结果不完整')
        if payload.get('code') != 0 or not payload.get('result'):
            raise RuntimeError(f"东财 {report_name} 返回错误: {payload.get('code')} {payload.get('message')}")
        result = payload['result']
        if not isinstance(result, dict):
            raise RuntimeError(f'东财 {report_name} 的 result 不是对象: {str(result)[:100]}')
        pages, count, data = (result.get('pages'), result.get('count'), result.get('data'))
        if data is None:
            data = []
        if not isinstance(data, list) or not all((isinstance(r, dict) for r in data)):
            raise RuntimeError(f'东财 {report_name} 第 {page} 页的 data 不是由对象组成的列表，格式可能已变')
        if any((isinstance(v, bool) or not isinstance(v, int) for v in (pages, count))) or pages < 1 or count < 0:
            raise RuntimeError(f'东财 {report_name} 缺少分页信息（pages={pages!r}, count={count!r}）')
        if first is None:
            first = (pages, count)
        elif (pages, count) != first:
            raise RuntimeError(f'东财 {report_name} 翻页时总页数 / 总条数从 {first} 变成 {(pages, count)}，结果可能错位，请重试')
        if not data and (page > 1 or pages > 1):
            raise RuntimeError(f'东财 {report_name} 第 {page}/{pages} 页是空的，结果不完整')
        if page < pages and len(data) != int(page_size):
            raise RuntimeError(f'东财 {report_name} 第 {page}/{pages} 页只有 {len(data)} 条（非末页应为 {page_size} 条），结果不完整')
        rows.extend(data)
        if len(rows) >= max_rows and count > max_rows:
            return rows[:max_rows]
        if page >= pages:
            if len(rows) != count:
                raise RuntimeError(f'东财 {report_name} 翻页后 {len(rows)} 条，与总数 {count} 不符')
            return rows
        page += 1

def _em_day(value):
    """东财日期串 '2026-09-18 00:00:00' → '2026-09-18'；空值 → None；认不出的写法抛 RuntimeError
    （只截前 10 个字符会把 '2026/09/18' 原样放行，再拿去和日期串比较就会比错）。"""
    return _v39_src_date(str(value)[:10]) if value else None

# ============ layer1 ============
import urllib.request

def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """
    批量拉取腾讯财经实时行情。
    codes: ["688017", "300476", "002463"]
    也支持指数: ["000001", "000300", "399006"]
    也支持ETF: ["510050", "510300"]
    返回: {code: {name, price, pe_ttm, pb, mcap, ...}}
    """
    SH_INDEX = {'000300', '000905', '000016', '000688', '000852', '000010'}
    prefixed = []
    key_of = {}
    for c in codes:
        low = c.lower()
        if low.startswith(('sh', 'sz', 'bj')):
            p = low
        elif c.startswith('92'):
            p = f'bj{c}'
        elif c in SH_INDEX or c.startswith(('5', '6', '9')):
            p = f'sh{c}'
        elif c.startswith(('4', '8')):
            p = f'bj{c}'
        else:
            p = f'sz{c}'
        prefixed.append(p)
        key_of[p] = c
    url = 'https://qt.gtimg.cn/q=' + ','.join(prefixed)
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode('gbk')
    result = {}
    for line in data.strip().split(';'):
        if not line.strip() or '=' not in line or '"' not in line:
            continue
        key = line.split('=')[0].split('_')[-1]
        vals = line.split('"')[1].split('~')
        if len(vals) < 53:
            continue
        code = key_of.get(key, key[2:])
        result[code] = {'name': vals[1], 'price': float(vals[3]) if vals[3] else 0, 'last_close': float(vals[4]) if vals[4] else 0, 'open': float(vals[5]) if vals[5] else 0, 'change_amt': float(vals[31]) if vals[31] else 0, 'change_pct': float(vals[32]) if vals[32] else 0, 'high': float(vals[33]) if vals[33] else 0, 'low': float(vals[34]) if vals[34] else 0, 'amount_wan': float(vals[37]) if vals[37] else 0, 'turnover_pct': float(vals[38]) if vals[38] else 0, 'pe_ttm': float(vals[39]) if vals[39] else 0, 'amplitude_pct': float(vals[43]) if vals[43] else 0, 'float_mcap_yi': float(vals[44]) if vals[44] else 0, 'mcap_yi': float(vals[45]) if vals[45] else 0, 'pb': float(vals[46]) if vals[46] else 0, 'limit_up': float(vals[47]) if vals[47] else 0, 'limit_down': float(vals[48]) if vals[48] else 0, 'vol_ratio': float(vals[49]) if vals[49] else 0, 'pe_static': float(vals[52]) if vals[52] else 0}
        q = result[code]
        q['is_stale'] = q['amount_wan'] == 0 and q['price'] == q['last_close'] and (q['price'] > 0)
        if q['is_stale'] and key[2:4] in ('43', '83', '87'):
            q['stale_reason'] = '北交所老号段，多数已迁至 920xxx，请按名称反查现行代码'
        elif q['is_stale']:
            q['stale_reason'] = '成交量为 0（停牌 / 未开盘 / 废码），报价非当日真实成交'
    return result
quotes = tencent_quote(['688017', '300476', '002463'])
index_quotes = tencent_quote(['000001', '000300', '399006'])
etf_quotes = tencent_quote(['510050', '510300'])
import time
from datetime import date, datetime, timedelta
TENCENT_KLINE_HOSTS = ['https://web.ifzq.gtimg.cn', 'https://proxy.finance.qq.com/ifzqgtimg', 'https://ifzq.gtimg.cn']
_TENCENT_HOST_COOLDOWN = 120
_tencent_host_down_until = {}
_TENCENT_MINUTES = ('m1', 'm5', 'm15', 'm30', 'm60')
_TENCENT_SPAN_DAYS = {'day': 700, 'week': 3650, 'month': 18250}

def _tencent_kline_call(path, param):
    """按顺序尝试三个入口；空响应或异常视为该入口被限流，冷却后换下一个。返回 (data, 实际成功的入口)。"""
    errors = []
    for host in TENCENT_KLINE_HOSTS:
        if _tencent_host_down_until.get(host, 0) > time.time():
            continue
        try:
            response = _v39_http(host + path, params={'param': param}, headers={'Referer': 'https://gu.qq.com/'}, timeout=(8, 20))
            payload = _v39_json(response) if response.text.strip() else {}
        except RuntimeError as exc:
            errors.append(f'{host}: {type(exc.__cause__ or exc).__name__}')
            _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
            continue
        if not isinstance(payload, dict):
            errors.append(f'{host}: 顶层是 {type(payload).__name__}，不是对象')
            _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
            continue
        if payload.get('msg') == 'param error':
            raise ValueError(f'腾讯 K 线参数错误（区间过长或代码不存在）: {param}')
        if isinstance(payload.get('data'), dict) and payload['data']:
            return (payload['data'], host)
        errors.append(f'{host}: 空响应')
        _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
    raise RuntimeError('腾讯 K 线三个入口均不可用（可能被限流，稍后重试）: ' + '; '.join(errors))

@_v39_contract
def tencent_kline(code, period='day', adjust=None, start=None, end=None, count=320):
    """腾讯 K 线 — 日/周/月（默认前复权）与 1/5/15/30/60 分钟（不复权）。

    period: day / week / month / m1 / m5 / m15 / m30 / m60
    adjust: None=日周月默认 qfq、分钟默认不复权；可显式传 'qfq' / 'hfq' / ''（不复权）
    start/end: 仅日周月可用，'YYYY-MM-DD'；给了 start 会自动按段分页（单次最多 640 根）
    count: 不给 start 时取最近 count 根；日周月 ≤ 640，分钟 ≤ 320
    成交量单位：科创板（688/689）是股，其余是手；科创板以外，最近一个交易日的分钟量是整手、下一个交易日才回填成精确股数（见 §1.2 说明）。
    本接口**没有成交额**，需要成交额用 §1.3 通达信盘后包。
    不支持北交所：腾讯对北交所只返回最新 1 根日线，区间与分钟线为空（2026-09-20 实测），直接抛 ValueError。
    """
    period = str(period).lower()
    if get_prefix(code) == 'bj':
        raise ValueError('腾讯 K 线不支持北交所（只返回最新 1 根日线、分钟线为空）；北交所日线请用 §1.3 tdx_daily_package(date) 按交易日取')
    symbol = get_prefix(code) + norm_ticker(code)
    if period in _TENCENT_MINUTES:
        if adjust not in (None, ''):
            raise ValueError('分钟线只有不复权数据，adjust 请留空')
        if start or end:
            raise ValueError('分钟线只能取最近 count 根，不支持 start/end')
        if not 1 <= int(count) <= 320:
            raise ValueError('分钟线 count 范围 1–320')
        data, host = _tencent_kline_call('/appstock/app/kline/mkline', f'{symbol},{period},,{int(count)}')
        node = data.get(symbol)
        if not isinstance(node, dict) or not isinstance(node.get(period), list):
            raise RuntimeError(f'腾讯分钟线 {symbol} 的返回里没有 {period} 列表，格式可能已变')
        rows, seen = ([], set())
        try:
            for item in node[period]:
                stamp = datetime.strptime(item[0], '%Y%m%d%H%M')
                if stamp in seen:
                    raise RuntimeError(f'腾讯分钟线 {symbol} 同一时刻 {stamp} 出现两次，结果不可信')
                seen.add(stamp)
                rows.append({'datetime': stamp.strftime('%Y-%m-%d %H:%M'), 'open': _v39_req_num(item[1], 'open'), 'high': _v39_req_num(item[3], 'high'), 'low': _v39_req_num(item[4], 'low'), 'close': _v39_req_num(item[2], 'close'), 'volume': _v39_req_num(item[5], 'volume'), 'turnover_rate_pct': _v39_num(item[7]) / 100 if len(item) > 7 and _v39_num(item[7]) is not None else None})
        except (ValueError, TypeError, IndexError, KeyError) as exc:
            raise RuntimeError(f'腾讯分钟线 {symbol} 行格式改变: {exc}') from exc
        if not rows:
            raise RuntimeError(f'腾讯分钟线 {symbol} {period} 返回 0 根')
        frame = _v39_frame(rows, 'tencent', host + '/appstock/app/kline/mkline')
        frame.insert(0, 'code', symbol)
        return frame
    if period not in _TENCENT_SPAN_DAYS:
        raise ValueError('period 只能是 day/week/month 或 m1/m5/m15/m30/m60')
    adjust = 'qfq' if adjust is None else adjust
    if adjust not in ('qfq', 'hfq', ''):
        raise ValueError("adjust 只能是 'qfq' / 'hfq' / ''")
    if start:
        first = datetime.strptime(_v39_date(start), '%Y-%m-%d').date()
        last = datetime.strptime(_v39_date(end), '%Y-%m-%d').date() if end else date.today()
        if first > last:
            raise ValueError('start 不能晚于 end')
        windows, cursor = ([], first)
        while cursor <= last:
            stop = min(cursor + timedelta(days=_TENCENT_SPAN_DAYS[period] - 1), last)
            windows.append((cursor.isoformat(), stop.isoformat(), 640))
            cursor = stop + timedelta(days=1)
    else:
        if end:
            raise ValueError('只给 end 时请同时给 start')
        if not 1 <= int(count) <= 640:
            raise ValueError('日周月 count 范围 1–640')
        windows = [('', '', int(count))]
    by_date, used_hosts = ({}, [])
    for s, e, n in windows:
        data, host = _tencent_kline_call('/appstock/app/fqkline/get', f'{symbol},{period},{s},{e},{n},{adjust}')
        if host not in used_hosts:
            used_hosts.append(host)
        node = data.get(symbol)
        key = adjust + period
        if not isinstance(node, dict) or not isinstance(node.get(key, node.get(period)), list):
            raise RuntimeError(f"腾讯 K 线 {symbol} {s or '最近'}~{e or ''} 的返回里没有 {key} / {period}，格式可能已变")
        items = node.get(key, node.get(period))
        if key != period and key in node and (not items) and node.get(period):
            raise RuntimeError(f'腾讯 K 线 {symbol} {s}~{e} 的 {key} 为空、{period} 却有数据，不能拿原始价冒充复权价')
        try:
            for item in items:
                day = _v39_src_date(item[0])
                if s and (not s <= day <= e):
                    raise RuntimeError(f'腾讯 K 线 {symbol} 请求 {s}~{e} 却返回了 {day}，结果不可信')
                if day in by_date:
                    raise RuntimeError(f'腾讯 K 线 {symbol} 日期 {day} 出现两次，结果不可信')
                by_date[day] = {'date': day, 'open': _v39_req_num(item[1], 'open'), 'high': _v39_req_num(item[3], 'high'), 'low': _v39_req_num(item[4], 'low'), 'close': _v39_req_num(item[2], 'close'), 'volume': _v39_req_num(item[5], 'volume')}
        except (ValueError, TypeError, IndexError, KeyError) as exc:
            raise RuntimeError(f'腾讯 K 线 {symbol} 行格式改变: {exc}') from exc
    rows = [by_date[k] for k in sorted(by_date)]
    if start:
        rows = [r for r in rows if windows[0][0] <= r['date'] <= windows[-1][1]]
    if not rows:
        raise RuntimeError(f'腾讯 K 线 {symbol} {period} 在所给区间内 0 根（未上市/停牌区间/代码有误）')
    if any((min(r['open'], r['high'], r['low'], r['close']) <= 0 for r in rows)):
        raise RuntimeError(f"腾讯 {adjust or '原始'} 价格出现 ≤0（等差复权口径的副作用）；请改用 adjust='' 取不复权价，再用 §1.6 sina_adjust_factor + apply_adjust")
    frame = _v39_frame(rows, 'tencent', ' | '.join((h + '/appstock/app/fqkline/get' for h in used_hosts)))
    frame.insert(0, 'code', symbol)
    frame.insert(1, 'adjust', adjust or 'none')
    return frame
day = tencent_kline('600519', start='2025-01-01')
raw = tencent_kline('600519', adjust='', count=250)
m5 = tencent_kline('300750', period='m5', count=96)
idx = tencent_kline('sh000001', period='week', count=100)
import io
import math
import re
import struct
import zipfile
import zlib
TDX_PACKAGE_URL = 'https://www.tdx.com.cn/products/data/data/g4day/{ymd}.zip'
TDX_BJ_FIRST_DAY = '20220506'
TDX_MIN_PRICED = {'sh': 10000, 'sz': 3000, 'bj': 50}

def _tdx_parse_package(content, ymd):
    """解析通达信每日增量包：每个市场一对 .cod（代码表，150 字节/条）+ .md1（行情块，512 字节/块）。
    布局参考 jing2uo/tdx2db（MIT）的 tdx/merge.go，已用 600519/000001/920000 与腾讯收盘价对拍。"""
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())
    rows = []
    for market in ('sh', 'sz', 'bj'):
        cod_name, md1_name = (f'{market}{ymd[2:]}.cod', f'{market}{ymd[2:]}.md1')
        if market == 'bj' and ymd < TDX_BJ_FIRST_DAY and (cod_name not in names) and (md1_name not in names):
            continue
        if cod_name not in names or md1_name not in names:
            raise RuntimeError(f'通达信盘后包缺少 {cod_name}/{md1_name}，格式可能已变')
        cod, md1 = (archive.read(cod_name), archive.read(md1_name))
        if len(cod) % 150 or len(md1) % 512:
            raise RuntimeError(f'{market} 代码表或行情块长度不是整块，文件可能被截断')
        if len(cod) // 150 != len(md1) // 512:
            raise RuntimeError(f'{market} 代码表 {len(cod) // 150} 条、行情块 {len(md1) // 512} 块，对不上')
        before, codes, seqs = (len(rows), set(), set())
        for offset in range(0, len(cod), 150):
            record = cod[offset:offset + 150]
            code = record[0:6].rstrip(b'\x00 ').decode('ascii', 'replace')
            seq = struct.unpack('<H', record[32:34])[0]
            if not re.fullmatch('[0-9]{6}', code):
                raise RuntimeError(f'通达信盘后包 {market} 代码表出现非 6 位数字代码 {code!r}，格式可能已变')
            if code in codes or seq in seqs:
                raise RuntimeError(f'通达信盘后包 {market} 代码表有重复的代码 / 行情块序号（{code!r}, seq={seq}）')
            codes.add(code)
            seqs.add(seq)
            block = md1[seq * 512:(seq + 1) * 512]
            if len(block) != 512:
                raise RuntimeError(f'{market}{code} 行情块越界（seq={seq}）')
            prev_close = struct.unpack('<d', block[4:12])[0]
            open_, high, low, close = struct.unpack('<4d', block[12:44])
            amount = struct.unpack('<d', block[72:80])[0]
            if not all((math.isfinite(v) for v in (prev_close, open_, high, low, close, amount))):
                raise RuntimeError(f'通达信盘后包 {market}{code} 行情块出现非有限数值，文件可能已损坏')
            if close <= 0:
                continue
            volume = struct.unpack('<Q', block[56:64])[0]
            raw_name = record[40:72].split(b'\x00')[0]
            try:
                name = raw_name.decode('gbk').strip()
            except UnicodeDecodeError as exc:
                raise RuntimeError(f'通达信盘后包 {market}{code} 的名称不是 GBK，文件可能已损坏') from exc
            if not name:
                raise RuntimeError(f'通达信盘后包 {market}{code} 有价格却没有名称，文件可能已损坏')
            rows.append({'date': f'{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}', 'market': market, 'code': code, 'name': name, 'prev_close': round(prev_close, 4), 'open': round(open_, 4), 'high': round(high, 4), 'low': round(low, 4), 'close': round(close, 4), 'volume': volume, 'amount': round(amount, 2)})
        if len(rows) - before < TDX_MIN_PRICED[market]:
            raise RuntimeError(f'通达信盘后包 {market} 市场只有 {len(rows) - before} 条有价记录（实测下限 {TDX_MIN_PRICED[market]}），文件可能残缺或格式已变')
    return rows

@_v39_contract
def tdx_daily_package(date):
    """通达信官网每日盘后包 — 某一交易日沪深北全部证券的日线（含成交额）。

    走 HTTP 下载（约 2.7MB），与 #52 失效的 TCP 行情命令是两条路。
    个股 volume 单位是「股」、amount 单位是「元」；指数等特殊代码的 volume 为通达信原值。
    非交易日或当日包尚未发布时官网返回 404，本函数抛 ValueError，不返回空表。
    历史包实测 2022-01-04、2023-01-03 可取，2021-01-04 已 404，未逐日验证；
    2022-05-06 之前的包没有北交所文件，只返回沪深，之后缺北交所文件会报错。
    某个市场有价记录少于 TDX_MIN_PRICED 的实测下限、代码不是 6 位数字或重复、行情块序号重复、
    代码表与行情块条数对不上、价格 / 成交额不是有限数，
    都按文件残缺抛 RuntimeError，不把部分市场当全市场返回。
    """
    ymd = _v39_date(date).replace('-', '')
    url = TDX_PACKAGE_URL.format(ymd=ymd)
    response = _v39_http(url, timeout=(10, 90), allow_status=(404,))
    if response.status_code == 404:
        raise ValueError(f'{date} 没有通达信盘后包：非交易日、当日包尚未发布（通常收盘后数小时），或早于官网保留范围（实测 2021-01-04 已没有）')
    if not response.content.startswith(b'PK'):
        raise RuntimeError('通达信盘后包不是 zip 文件，可能是错误页')
    try:
        rows = _tdx_parse_package(response.content, ymd)
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise RuntimeError(f'通达信盘后包 {url} 无法解压: {type(exc).__name__}: {exc}') from exc
    return _v39_frame(rows, 'tdx', url)
snap = tdx_daily_package('2026-09-18')
import re
import time
TENCENT_TICK_URL = 'https://stock.gtimg.cn/data/index.php'
TENCENT_QT_URL = 'https://qt.gtimg.cn/q='
_TICK_MAX_PAGES = 300
_TICK_SESSION_END = '15:00:59'

def _tencent_qt_snapshot(symbol):
    """腾讯行情快照 → (交易日 'YYYY-MM-DD', 时刻 'HHMMSS', 当日成交额 元, 盘后定价成交额 元)。代码不存在抛 ValueError。
    盘后定价成交额是下标 58 的字段（万元、4 位小数）；字段没有或认不出时为 None，只是不做盘后核对。"""
    response = _v39_http(TENCENT_QT_URL + symbol, timeout=(5, 15), session=V39_RETRY_SESSION)
    text = response.content.decode('gbk', 'replace')
    if 'v_pv_none_match' in text:
        raise ValueError(f'腾讯没有 {symbol} 这个代码')
    match = re.search(f'v_{symbol}="([^"]*)"', text)
    if not match:
        raise RuntimeError(f'腾讯行情快照 {symbol} 的返回里没有 v_{symbol} 变量，格式可能已变')
    fields = match.group(1).split('~')
    if len(fields) < 36 or not re.fullmatch('[0-9]{14}', fields[30]):
        raise RuntimeError(f'腾讯行情快照 {symbol} 字段数 {len(fields)} 或时间字段不对，格式可能已变')
    parts = fields[35].split('/')
    if len(parts) != 3:
        raise RuntimeError(f'腾讯行情快照 {symbol} 的价/量/额字段是 {fields[35]!r}，格式可能已变')
    after_hours = fields[58] if len(fields) > 58 else ''
    after_hours = round(float(after_hours) * 10000.0, 2) if re.fullmatch('\\d+(\\.\\d+)?', after_hours) else None
    return (_v39_src_date(fields[30][:8]), fields[30][8:], _v39_req_num(parts[2], '成交额'), after_hours)

def _tencent_tick_page(symbol, page):
    """第 page 页逐笔（0 起）→ 记录列表；翻过最后一页时腾讯返回空内容，返回 None。"""
    response = _v39_http(TENCENT_TICK_URL, params={'appn': 'detail', 'action': 'data', 'c': symbol, 'p': page}, timeout=(5, 15), session=V39_RETRY_SESSION)
    text = response.content.decode('gbk', 'replace').strip()
    if not text:
        return None
    match = re.fullmatch(f'v_detail_data_{symbol}=\\[(\\d+),"([^"]*)"\\];?', text)
    if not match or int(match.group(1)) != page:
        raise RuntimeError(f'腾讯逐笔 {symbol} 第 {page} 页不是预期格式: {text[:80]!r}')
    if not match.group(2):
        return None
    records = []
    try:
        for item in match.group(2).split('|'):
            seq, clock, price, change, volume, amount, side = item.split('/')
            if not re.fullmatch('\\d\\d:\\d\\d:\\d\\d', clock) or side not in ('B', 'S', 'M'):
                raise ValueError(item)
            records.append({'seq': int(seq), 'time': clock, 'price': _v39_req_num(price, 'price'), 'change': _v39_req_num(change, 'change'), 'volume': _v39_req_num(volume, 'volume'), 'amount': _v39_req_num(amount, 'amount'), 'side': side})
    except ValueError as exc:
        raise RuntimeError(f'腾讯逐笔 {symbol} 第 {page} 页记录格式改变: {exc}') from exc
    return records

@_v39_contract
def tencent_ticks(code):
    """腾讯逐笔成交（分笔）— 最近一个交易日的全部成交明细，沪深个股与 ETF。

    一行一笔：date / code / time / seq（腾讯序号）/ price / change（较上一笔）/ volume（手）/ amount（元）/
    side（B 主动买 · S 主动卖 · M 中性）。约 3 秒一笔的分笔，不是 Level-2 逐笔。
    北交所、指数、代码不存在、当日没有成交抛 ValueError。收盘后调用会用行情快照的当日成交额核对连续竞价段，
    对不上抛 RuntimeError；盘后定价段腾讯偶尔缺几笔：中间缺的序号在 frame.attrs["missing_seq"]，
    15:31 以后再用快照的盘后成交额核对，结果在 frame.attrs["complete"]（True 对上 / False 有缺 / None 没法核对；
    低价 ETF 一两手的小额缺笔可能看不出）。
    """
    prefix, ticker = (get_prefix(code), norm_ticker(code))
    if prefix == 'bj':
        raise ValueError('腾讯逐笔不支持北交所（返回空）；北交所日线见 §1.3 tdx_daily_package')
    if (prefix, ticker[:3]) in (('sh', '000'), ('sz', '399')):
        raise ValueError(f'{prefix}{ticker} 是指数，没有逐笔成交')
    symbol = prefix + ticker
    day, clock, amount_before, after_hours_before = _tencent_qt_snapshot(symbol)
    if amount_before == 0:
        raise ValueError(f'{symbol} 在 {day} 没有成交（停牌、尚未开盘或集合竞价未撮合）')
    rows, missing = ([], [])
    for page in range(_TICK_MAX_PAGES):
        records = _tencent_tick_page(symbol, page)
        if records is None:
            break
        for r in records:
            expected = rows[-1]['seq'] + 1 if rows else 0
            if r['seq'] < expected or (rows and r['time'] < rows[-1]['time']):
                raise RuntimeError(f"腾讯逐笔 {symbol} 序号或时间倒退（第 {page} 页 {r['seq']} {r['time']}），结果不可信")
            if r['seq'] > expected:
                if r['time'] <= _TICK_SESSION_END:
                    raise RuntimeError(f"腾讯逐笔 {symbol} 缺序号 {expected}–{r['seq'] - 1}（{r['time']} 之前，第 {page} 页），腾讯该页缓存不完整，稍后重试")
                missing.extend(range(expected, r['seq']))
            rows.append(r)
        time.sleep(0.1)
    else:
        raise RuntimeError(f'腾讯逐笔 {symbol} 翻到第 {_TICK_MAX_PAGES} 页仍未结束，格式可能已变')
    if not rows:
        if clock < '092500':
            raise ValueError(f'{symbol} 集合竞价尚未撮合（{clock}），还没有逐笔')
        raise RuntimeError(f'{symbol} 在 {day} 成交 {amount_before:.0f} 元，腾讯逐笔却为空：开盘前腾讯可能已清空上一交易日的明细，否则是接口变了')
    day_after, clock_after, amount_after, after_hours_after = _tencent_qt_snapshot(symbol)
    if day_after != day:
        raise RuntimeError(f'取数期间交易日从 {day} 变成 {day_after}，请重试')
    session = sum((r['amount'] for r in rows if r['time'] <= _TICK_SESSION_END))
    if amount_after == amount_before and abs(session - amount_before) > amount_before * 0.001 + 1000:
        raise RuntimeError(f'腾讯逐笔 {symbol} 连续竞价段成交额 {session:.0f} 元，与行情快照 {amount_before:.0f} 元对不上，逐笔可能不全')
    after_hours = [r['amount'] for r in rows if r['time'] > _TICK_SESSION_END]
    complete = None
    if missing:
        complete = False
    elif amount_after == amount_before and after_hours_before is not None and (after_hours_after == after_hours_before) and (clock >= '153100'):
        complete = abs(sum(after_hours) - after_hours_before) <= len(after_hours) + 1
    frame = _v39_frame(rows, 'tencent', f'{TENCENT_TICK_URL}?appn=detail&action=data&c={symbol}', ['time', 'seq', 'price', 'change', 'volume', 'amount', 'side'])
    frame.insert(0, 'date', day)
    frame.insert(1, 'code', symbol)
    frame.attrs['missing_seq'] = missing
    frame.attrs['complete'] = complete
    return frame
ticks = tencent_ticks('000001')
auction = ticks[ticks.time < '09:30:00']
buy = ticks.loc[ticks.side == 'B', 'amount'].sum()
etf = tencent_ticks('510300')
import requests

def baidu_kline_with_ma(code: str, start_time: str='') -> dict:
    """百度股市通K线 — 独有能力: 返回时自带 ma5/ma10/ma20 均价"""
    url = 'https://finance.pae.baidu.com/selfselect/getstockquotation'
    params = {'all': '1', 'isIndex': 'false', 'isBk': 'false', 'isBlock': 'false', 'isFutures': 'false', 'isStock': 'true', 'newFormat': '1', 'group': 'quotation_kline_ab', 'finClientType': 'pc', 'code': code, 'start_time': start_time, 'ktype': '1'}
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/vnd.finance-web.v1+json', 'Origin': 'https://gushitong.baidu.com', 'Referer': 'https://gushitong.baidu.com/'}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    d = r.json()
    result = d.get('Result', {})
    md = result.get('newMarketData', {})
    keys = md.get('keys', [])
    rows = md.get('marketData', '').split(';')
    return {'keys': keys, 'rows': rows}
data = baidu_kline_with_ma('600519')
import json
import re
import requests

def sina_adjust_factor(code: str, kind: str='qfq') -> list:
    """新浪复权因子序列 — kind='qfq'(前复权) | 'hfq'(后复权)，按日期倒序（最新在前）"""
    if kind not in ('qfq', 'hfq'):
        raise ValueError(f"kind 只能是 'qfq' 或 'hfq'，收到 {kind!r}")
    raw = str(code).strip()
    digits = norm_ticker(raw)
    m = re.match('^(sh|sz|bj)', raw, re.I) or re.search('\\.(sh|sz|bj|xshg|xshe)$', raw, re.I)
    prefix = {'xshg': 'sh', 'xshe': 'sz'}.get(m.group(1).lower(), m.group(1).lower()) if m else get_prefix(digits)
    symbol = f'{prefix}{digits}'
    url = f'https://finance.sina.com.cn/realstock/company/{symbol}/{kind}.js'
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}, timeout=10)
    r.raise_for_status()
    text = r.text
    brace = text.find('{')
    if brace < 0:
        raise RuntimeError(f'新浪复权因子响应无 JSON（{symbol}/{kind}）: {text[:120]}')
    try:
        data, _ = json.JSONDecoder().raw_decode(text[brace:])
    except json.JSONDecodeError as e:
        raise RuntimeError(f'新浪复权因子 JSON 解析失败（{symbol}/{kind}）: {e}') from e
    return [{'date': it['d'], 'factor': float(it['f'])} for it in data.get('data', [])]

def apply_adjust(bars, factors: list, kind: str='qfq', price_keys=('open', 'high', 'low', 'close')):
    """把复权因子套到不复权 K 线上。

    `bars` 接受两种形态：
      - **§1.7 `tdx_client().bars()` 的 DataFrame**（日期列名是 `datetime`）或 §1.2 `tencent_kline(adjust='')` 的 DataFrame（`date` 列）→ 返回 DataFrame
      - list[dict]（需含 `date` 键）→ 返回 list[dict]

    🔴 **qfq 与 hfq 的运算方向相反，必须传对 kind**：
      - `qfq`（前复权）因子是**除数**：`前复权价 = 不复权价 ÷ factor`
      - `hfq`（后复权）因子是**乘数**：`后复权价 = 不复权价 × factor`
    传错方向不会报错，只会把历史价格放大/缩小几倍（见下方实测对照表）。

    因子表是「生效日 → 因子」的阶梯，每根 K 线取**不晚于它**的最近一个因子。
    """
    if kind not in ('qfq', 'hfq'):
        raise ValueError(f"kind 只能是 'qfq' 或 'hfq'，收到 {kind!r}")
    if not factors:
        raise ValueError('复权因子列表为空，无法复权。请先确认 sina_adjust_factor() 是否取到数据（新浪对不支持的标的会返回空 data），不要用未复权价继续计算。')
    is_df = hasattr(bars, 'columns') and hasattr(bars, 'to_dict')
    if is_df:
        date_col = next((c for c in ('date', 'datetime') if c in bars.columns), None)
        if date_col is None:
            raise ValueError(f'DataFrame 需含 date 或 datetime 列，实际列={list(bars.columns)}')
        rows = bars.to_dict('records')
        for r in rows:
            r['date'] = str(r[date_col])[:10]
    else:
        rows = [dict(b) for b in bars]
        for r in rows:
            if 'date' not in r:
                raise ValueError(f"每根 K 线需含 'date' 键，实际键={sorted(r)}")
            r['date'] = str(r['date'])[:10]
    fac = sorted(factors, key=lambda x: x['date'])
    out, i, cur = ([], 0, None)
    for bar in sorted(rows, key=lambda b: b['date']):
        while i < len(fac) and fac[i]['date'] <= bar['date']:
            cur = fac[i]['factor']
            i += 1
        if cur is None:
            raise RuntimeError(f"K 线日期 {bar['date']} 早于因子序列最早日 {fac[0]['date']}，无法复权；不返回未复权价以免与已复权行混淆。")
        if cur == 0:
            raise RuntimeError(f"复权因子为 0（{bar['date']}），无法换算")
        nb = dict(bar)
        for k in price_keys:
            if k in nb and nb[k] is not None:
                v = float(nb[k])
                nb[k] = round(v / cur if kind == 'qfq' else v * cur, 4)
        nb['adj_factor'] = cur
        out.append(nb)
    if is_df:
        import pandas as pd
        res = pd.DataFrame(out)
        if getattr(bars, 'index', None) is not None and (not isinstance(bars.index, pd.RangeIndex)):
            order = sorted(range(len(bars)), key=lambda n: str(bars.iloc[n][date_col])[:10])
            res.index = bars.index[order]
            res.index.name = bars.index.name
        return res
    return out
qfq = sina_adjust_factor('600519', 'qfq')
hfq = sina_adjust_factor('600519', 'hfq')
bars = [{'date': '2015-01-05', 'close': 202.52}]
from mootdx.quotes import Quotes
client = tdx_client()
klines = client.bars(symbol='688017', frequency=9, offset=10)
min1 = client.bars(symbol='688017', frequency=8, offset=240)
min5 = client.bars(symbol='688017', frequency=0, offset=48)
quotes = client.quotes(symbol=['688017', '300476'])
trades = client.transaction(symbol='688017', date='20260502')

# ============ layer3 ============
import requests
import pandas as pd

def ths_hot_reason(date: str=None) -> pd.DataFrame:
    """
    同花顺当日强势股归因。
    date: 'YYYY-MM-DD' 格式，None=今天
    返回 DataFrame，含每只股票的题材标签 (reason)。

    实测: 73ms 拿到 ~125 只 + 完整字段
    """
    from datetime import date as _date
    if date is None:
        date = _date.today().strftime('%Y-%m-%d')
    url = f'http://zx.10jqka.com.cn/event/api/getharden/date/{date}/orderby/date/orderway/desc/charset/GBK/'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36'}
    r = requests.get(url, headers=headers, timeout=10)
    data = r.json()
    if data.get('errocode', 0) != 0:
        raise RuntimeError(f"同花顺热点错误: {data.get('errormsg', '')}")
    rows = data.get('data') or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    rename_map = {'name': '名称', 'code': '代码', 'reason': '题材归因', 'close': '收盘价', 'zhangdie': '涨跌额', 'zhangfu': '涨幅%', 'huanshou': '换手率%', 'chengjiaoe': '成交额', 'chengjiaoliang': '成交量', 'ddejingliang': '大单净量', 'market': '市场'}
    df = df.rename(columns=rename_map)
    return df
df = ths_hot_reason('2026-05-09')
import requests
import pandas as pd
from pathlib import Path
HSGT_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36', 'Host': 'data.hexin.cn', 'Referer': 'https://data.hexin.cn/'}

def hsgt_realtime() -> pd.DataFrame:
    """
    沪深股通当日实时分钟流向（含集合竞价 09:10–15:00，262 个时间点）。
    返回字段: time, hgt(沪股通累计净买入), sgt(深股通累计净买入)
    单位: 亿元
    """
    url = 'https://data.hexin.cn/market/hsgtApi/method/dayChart/'
    r = requests.get(url, headers=HSGT_HEADERS, timeout=10)
    d = r.json()
    times = d.get('time', [])
    hgt = d.get('hgt', [])
    sgt = d.get('sgt', [])
    n = len(times)
    return pd.DataFrame({'time': times, 'hgt_yi': hgt[:n] + [None] * (n - len(hgt)), 'sgt_yi': sgt[:n] + [None] * (n - len(sgt))})

def _northbound_cache_path() -> Path:
    """北向资金本地 CSV 缓存路径"""
    p = Path.home() / '.tradingagents' / 'cache' / 'northbound_daily.csv'
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _save_northbound_snapshot(date: str, hgt: float, sgt: float):
    """写入/更新当天北向收盘数据到 CSV"""
    path = _northbound_cache_path()
    rows = {}
    if path.exists():
        for line in path.read_text().strip().split('\n')[1:]:
            parts = line.split(',')
            if len(parts) == 3:
                rows[parts[0]] = line
    rows[date] = f'{date},{hgt},{sgt}'
    with open(path, 'w') as f:
        f.write('date,hgt,sgt\n')
        for d in sorted(rows.keys()):
            f.write(rows[d] + '\n')

def _load_northbound_history(n: int=20) -> pd.DataFrame:
    """读取最近 N 天北向历史"""
    path = _northbound_cache_path()
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df.tail(n)
df = hsgt_realtime()
if not df.empty:
    last = df.dropna().iloc[-1]
    _save_northbound_snapshot('2026-05-17', last['hgt_yi'], last['sgt_yi'])
hist = _load_northbound_history(20)
def eastmoney_concept_blocks(code: str) -> dict:
    """
    个股所属板块/概念归属（东财 slist，一次请求拿全，已内置限流）。
    返回: {total, boards: [{name, code(BK码), change_pct, lead_stock}], concept_tags: [板块名...]}
    boards 混合 行业/概念/地域，板块名自解释；concept_tags 是所有板块名的便捷列表。
    """
    market_code = em_market_code(code)
    params = {'fltt': '2', 'invt': '2', 'secid': f'{market_code}.{code}', 'spt': '3', 'pi': '0', 'pz': '200', 'po': '1', 'fields': 'f12,f14,f3,f128'}
    headers = {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'}
    try:
        r = em_get('https://push2.eastmoney.com/api/qt/slist/get', params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        print(f'[WARN] 东财板块归属请求失败: {e}')
        return {'total': 0, 'boards': [], 'concept_tags': []}
    diff = (d.get('data') or {}).get('diff') or {}
    items = diff.values() if isinstance(diff, dict) else diff
    boards = []
    for it in items:
        boards.append({'name': it.get('f14', ''), 'code': it.get('f12', ''), 'change_pct': it.get('f3', ''), 'lead_stock': it.get('f128', '')})
    return {'total': len(boards), 'boards': boards, 'concept_tags': [b['name'] for b in boards]}
blocks = eastmoney_concept_blocks('600519')
import requests

def eastmoney_fund_flow_minute(code: str) -> list[dict]:
    """
    个股资金流向（分钟级，当日盘中）。
    code: 6位股票代码
    返回: [{time, main_net, small_net, mid_net, large_net, super_net}, ...]
    单位: 元
    """
    secid = em_secid(code)
    url = 'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get'
    params = {'secid': secid, 'klt': 1, 'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56,f57'}
    headers = {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/', 'Origin': 'https://quote.eastmoney.com'}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json()
    except Exception as e:
        print(f'[WARN] push2 资金流请求失败: {e}')
        return []
    rows = []
    for line in (d.get('data') or {}).get('klines') or []:
        parts = line.split(',')
        if len(parts) >= 6:
            rows.append({'time': parts[0], 'main_net': float(parts[1]), 'small_net': float(parts[2]), 'mid_net': float(parts[3]), 'large_net': float(parts[4]), 'super_net': float(parts[5])})
    return rows
realtime = eastmoney_fund_flow_minute('000858')
if realtime:
    last = realtime[-1]
    signal = 'bullish' if last['main_net'] > 0 else 'bearish'
    print(f"主力净流入: {last['main_net']:.0f}元 → {signal}")
    total = sum((r['main_net'] for r in realtime))
    print(f'全天主力累计: {total / 10000.0:.0f}万元')
import requests
from datetime import datetime, timedelta

def dragon_tiger_board(code: str, trade_date: str, look_back: int=30) -> dict:
    """
    龙虎榜数据聚合。
    trade_date: YYYY-MM-DD
    look_back: 回看天数
    返回: {records: [...], seats: {buy: [...], sell: [...]}, institution: {...}}
    """
    start = datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=look_back)
    start_str = start.strftime('%Y-%m-%d')
    records = []
    data = eastmoney_datacenter('RPT_DAILYBILLBOARD_DETAILSNEW', filter_str=f'''(TRADE_DATE>='{start_str}')(TRADE_DATE<='{trade_date}')(SECURITY_CODE="{code}")''', page_size=50, sort_columns='TRADE_DATE', sort_types='-1')
    for row in data:
        records.append({'date': str(row.get('TRADE_DATE', ''))[:10], 'reason': row.get('EXPLANATION', ''), 'net_buy': round((row.get('BILLBOARD_NET_AMT') or 0) / 10000, 1), 'turnover': round(float(row.get('TURNOVERRATE') or 0), 2)})
    buy_data, sell_data = ([], [])
    seats = {'buy': [], 'sell': []}
    if records:
        latest_date = records[0]['date']
        buy_data = eastmoney_datacenter('RPT_BILLBOARD_DAILYDETAILSBUY', filter_str=f'''(TRADE_DATE='{latest_date}')(SECURITY_CODE="{code}")''', page_size=10, sort_columns='BUY', sort_types='-1')
        for row in buy_data[:5]:
            seats['buy'].append({'name': row.get('OPERATEDEPT_NAME', ''), 'buy_amt': round((row.get('BUY') or 0) / 10000, 1), 'sell_amt': round((row.get('SELL') or 0) / 10000, 1), 'net': round((row.get('NET') or 0) / 10000, 1)})
        sell_data = eastmoney_datacenter('RPT_BILLBOARD_DAILYDETAILSSELL', filter_str=f'''(TRADE_DATE='{latest_date}')(SECURITY_CODE="{code}")''', page_size=10, sort_columns='SELL', sort_types='-1')
        for row in sell_data[:5]:
            seats['sell'].append({'name': row.get('OPERATEDEPT_NAME', ''), 'buy_amt': round((row.get('BUY') or 0) / 10000, 1), 'sell_amt': round((row.get('SELL') or 0) / 10000, 1), 'net': round((row.get('NET') or 0) / 10000, 1)})
    institution = {'buy_amt': 0, 'sell_amt': 0, 'net_amt': 0}
    for detail_data, side in [(buy_data, 'buy'), (sell_data, 'sell')]:
        for row in detail_data:
            if str(row.get('OPERATEDEPT_CODE', '')) == '0':
                amt = row.get('BUY') or 0 if side == 'buy' else row.get('SELL') or 0
                if side == 'buy':
                    institution['buy_amt'] += amt
                else:
                    institution['sell_amt'] += amt
    institution['buy_amt'] = round(institution['buy_amt'] / 10000, 1)
    institution['sell_amt'] = round(institution['sell_amt'] / 10000, 1)
    institution['net_amt'] = round(institution['buy_amt'] - institution['sell_amt'], 1)
    return {'records': records, 'seats': seats, 'institution': institution}
data = dragon_tiger_board('002475', '2026-05-17')
if data['seats']['buy']:
    print('买入席位 TOP5:')
    for s in data['seats']['buy']:
        print(f"  {s['name']}: 买{s['buy_amt']}万 卖{s['sell_amt']}万 净{s['net']}万")
from datetime import datetime, timedelta

def lockup_expiry(code: str, trade_date: str, forward_days: int=90) -> dict:
    """
    限售解禁日历。
    返回: {history: [...], upcoming: [...]}
    """
    history_data = eastmoney_datacenter('RPT_LIFT_STAGE', filter_str=f'(SECURITY_CODE="{code}")', page_size=15, sort_columns='FREE_DATE', sort_types='-1')
    history = []
    for row in history_data:
        history.append({'date': str(row.get('FREE_DATE', ''))[:10], 'type': row.get('FREE_SHARES_TYPE', ''), 'shares': row.get('FREE_SHARES', 0), 'able_shares': row.get('ABLE_FREE_SHARES', 0), 'ratio': row.get('FREE_RATIO', 0)})
    end_date = datetime.strptime(trade_date, '%Y-%m-%d') + timedelta(days=forward_days)
    end_str = end_date.strftime('%Y-%m-%d')
    upcoming_data = eastmoney_datacenter('RPT_LIFT_STAGE', filter_str=f"""(SECURITY_CODE="{code}")(FREE_DATE>='{trade_date}')(FREE_DATE<='{end_str}')""", page_size=20, sort_columns='FREE_DATE', sort_types='1')
    upcoming = []
    for row in upcoming_data:
        upcoming.append({'date': str(row.get('FREE_DATE', ''))[:10], 'type': row.get('FREE_SHARES_TYPE', ''), 'shares': row.get('FREE_SHARES', 0), 'able_shares': row.get('ABLE_FREE_SHARES', 0), 'ratio': row.get('FREE_RATIO', 0)})
    return {'history': history, 'upcoming': upcoming}
data = lockup_expiry('002475', '2026-05-17')
if data['upcoming']:
    print(f"未来90天待解禁 {len(data['upcoming'])} 批")
else:
    print('未来90天无待解禁')
import requests

def industry_comparison(top_n: int=20) -> dict:
    """
    全行业涨跌幅排名（东财行业板块，~100 个行业）。
    返回: {top: [...], bottom: [...], total: int}
    """
    url = 'https://push2.eastmoney.com/api/qt/clist/get'
    params = {'pn': '1', 'pz': '100', 'po': '1', 'np': '1', 'fltt': '2', 'invt': '2', 'fid': 'f3', 'fs': 'm:90+t:2', 'fields': 'f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207'}
    headers = {'User-Agent': UA}
    r = em_get(url, params=params, headers=headers, timeout=15)
    d = r.json()
    items = (d.get('data') or {}).get('diff') or []
    if not items:
        return {'top': [], 'bottom': [], 'total': 0}
    rows = []
    for i, item in enumerate(items):
        rows.append({'rank': i + 1, 'name': item.get('f14', ''), 'change_pct': item.get('f3', 0), 'code': item.get('f12', ''), 'up_count': item.get('f104', 0), 'down_count': item.get('f105', 0), 'leader': item.get('f140', ''), 'leader_change': item.get('f136', 0)})
    return {'top': rows[:top_n], 'bottom': rows[-top_n:], 'total': len(rows)}
data = industry_comparison(20)
import requests
_BOARD_FS = {'industry': 'm:90+t:2', 'concept': 'm:90+t:3', 'region': 'm:90+t:1'}
_BOARD_PERIOD = {'today': ('f62', 'f62', 'f184', 'f3', 'f204'), '5d': ('f164', 'f164', 'f165', 'f109', 'f257'), '10d': ('f174', 'f174', 'f175', 'f160', None)}

def board_fund_flow(board_type: str='industry', period: str='today', top_n: int=20) -> dict:
    """
    板块资金流向排名（按主力净流入降序）。
    board_type: industry(行业) / concept(概念) / region(地域)
    period:     today(今日) / 5d(5日) / 10d(10日)
    返回: {board_type, period, total, rows:[{rank, name, code, change_pct,
           main_net(主力净额,元), main_pct(主力净占比,%), leader(领涨股),
           # 仅 today：super_large_net/large_net/medium_net/small_net(超大/大/中/小单净额,元)}]}
    注：板块级只有 今日/5日/10日（无 3日，个股级才有）。主力净额 = 超大单 + 大单。
    """
    if board_type not in _BOARD_FS:
        raise ValueError(f'board_type 须为 {list(_BOARD_FS)}')
    if period not in _BOARD_PERIOD:
        raise ValueError(f'period 须为 {list(_BOARD_PERIOD)}')
    fid, f_main, f_pct, f_chg, f_leader = _BOARD_PERIOD[period]
    fields = ['f12', 'f14', f_chg, f_main, f_pct]
    if f_leader:
        fields.append(f_leader)
    if period == 'today':
        fields += ['f66', 'f72', 'f78', 'f84']
    url = 'https://push2.eastmoney.com/api/qt/clist/get'
    base = {'pz': '200', 'po': '1', 'np': '1', 'fltt': '2', 'invt': '2', 'fid': fid, 'fs': _BOARD_FS[board_type], 'fields': ','.join(dict.fromkeys(fields))}

    def _page(pn: int):
        r = em_get(url, params={**base, 'pn': str(pn)}, headers={'User-Agent': UA}, timeout=15)
        d = r.json().get('data') or {}
        return (d.get('diff') or [], int(d.get('total') or 0))
    _PAGE = 200
    items, total = _page(1)
    pn = 2
    while len(items) < top_n:
        if total and len(items) >= total:
            break
        more, _ = _page(pn)
        if not more:
            break
        items += more
        pn += 1
        if len(more) < _PAGE:
            break
    total = max(total, len(items))
    rows = []
    for i, it in enumerate(items):
        row = {'rank': i + 1, 'name': it.get('f14', ''), 'code': it.get('f12', ''), 'change_pct': it.get(f_chg, 0), 'main_net': it.get(f_main, 0), 'main_pct': it.get(f_pct, 0), 'leader': it.get(f_leader, '') if f_leader else ''}
        if period == 'today':
            row.update({'super_large_net': it.get('f66', 0), 'large_net': it.get('f72', 0), 'medium_net': it.get('f78', 0), 'small_net': it.get('f84', 0)})
        rows.append(row)
    return {'board_type': board_type, 'period': period, 'total': total, 'rows': rows[:top_n]}
d = board_fund_flow('industry', 'today', 10)
concept_5d = board_fund_flow('concept', '5d', 10)
region_10d = board_fund_flow('region', '10d', 10)
from datetime import datetime

def daily_dragon_tiger(trade_date: str=None, min_net_buy: float=None) -> dict:
    """
    全市场龙虎榜。
    trade_date: YYYY-MM-DD（默认当日）
    min_net_buy: 净买入下限（万元），None 不过滤
    返回: {date, total_records, stocks: [{code, name, reason, close, change_pct,
           net_buy_wan, buy_wan, sell_wan, turnover_pct}]}
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y-%m-%d')
    data = eastmoney_datacenter('RPT_DAILYBILLBOARD_DETAILSNEW', filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')", page_size=500, sort_columns='BILLBOARD_NET_AMT', sort_types='-1')
    if not data:
        return {'date': trade_date, 'total_records': 0, 'stocks': [], 'note': '无数据（非交易日或盘后未更新）'}
    actual_date = str(data[0].get('TRADE_DATE', ''))[:10] if data else trade_date
    stocks = []
    for row in data:
        net_buy = (row.get('BILLBOARD_NET_AMT') or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({'code': row.get('SECURITY_CODE', ''), 'name': row.get('SECURITY_NAME_ABBR', ''), 'reason': row.get('EXPLANATION', ''), 'close': row.get('CLOSE_PRICE') or 0, 'change_pct': round(float(row.get('CHANGE_RATE') or 0), 2), 'net_buy_wan': round(net_buy, 1), 'buy_wan': round((row.get('BILLBOARD_BUY_AMT') or 0) / 10000, 1), 'sell_wan': round((row.get('BILLBOARD_SELL_AMT') or 0) / 10000, 1), 'turnover_pct': round(float(row.get('TURNOVERRATE') or 0), 2)})
    return {'date': actual_date, 'total_records': len(stocks), 'stocks': stocks}
data = daily_dragon_tiger('2026-05-16')
data = daily_dragon_tiger('2026-05-16', min_net_buy=5000)
df_hot = ths_hot_reason()
from collections import Counter
all_tags = []
cnt = Counter(all_tags)
df_north = hsgt_realtime()
hgt_close = df_north['hgt_yi'].dropna().iloc[-1] if not df_north.empty else 0
sgt_close = df_north['sgt_yi'].dropna().iloc[-1] if not df_north.empty else 0
comp = industry_comparison(10)

# ============ layer4 ============
def margin_trading(code: str, page_size: int=30) -> list[dict]:
    """
    融资融券明细（日级）。
    返回: [{date, rzye(融资余额), rzmre(融资买入), rqye(融券余额), ...}]
    """
    data = eastmoney_datacenter('RPTA_WEB_RZRQ_GGMX', filter_str=f'(SCODE="{code}")', page_size=page_size, sort_columns='DATE', sort_types='-1')
    rows = []
    for row in data:
        rows.append({'date': str(row.get('DATE', ''))[:10], 'rzye': row.get('RZYE', 0), 'rzmre': row.get('RZMRE', 0), 'rzche': row.get('RZCHE', 0), 'rqye': row.get('RQYE', 0), 'rqmcl': row.get('RQMCL', 0), 'rqchl': row.get('RQCHL', 0), 'rzrqye': row.get('RZRQYE', 0)})
    return rows
data = margin_trading('600519')
def block_trade(code: str, page_size: int=20) -> list[dict]:
    """
    大宗交易记录。
    返回: [{date, price, vol, amount, buyer, seller, premium_pct}]
    """
    data = eastmoney_datacenter('RPT_DATA_BLOCKTRADE', filter_str=f'(SECURITY_CODE="{code}")', page_size=page_size, sort_columns='TRADE_DATE', sort_types='-1')
    rows = []
    for row in data:
        close = row.get('CLOSE_PRICE') or 0
        deal_price = row.get('DEAL_PRICE') or 0
        premium = (deal_price / close - 1) * 100 if close else 0
        rows.append({'date': str(row.get('TRADE_DATE', ''))[:10], 'price': deal_price, 'close': close, 'premium_pct': round(premium, 2), 'vol': row.get('DEAL_VOLUME', 0), 'amount': row.get('DEAL_AMT', 0), 'buyer': row.get('BUYER_NAME', ''), 'seller': row.get('SELLER_NAME', '')})
    return rows
data = block_trade('600519')
def holder_num_change(code: str, page_size: int=10) -> list[dict]:
    """
    股东户数变化（季度级）。
    返回: [{date, holder_num, change_num, change_ratio, avg_shares}]
    """
    data = eastmoney_datacenter('RPT_HOLDERNUMLATEST', filter_str=f'(SECURITY_CODE="{code}")', page_size=page_size, sort_columns='END_DATE', sort_types='-1')
    rows = []
    for row in data:
        rows.append({'date': str(row.get('END_DATE', ''))[:10], 'holder_num': row.get('HOLDER_NUM', 0), 'change_num': row.get('HOLDER_NUM_CHANGE', 0), 'change_ratio': row.get('HOLDER_NUM_RATIO', 0), 'avg_shares': row.get('AVG_FREE_SHARES', 0)})
    return rows
data = holder_num_change('600519')
def dividend_history(code: str, page_size: int=20) -> list[dict]:
    """
    分红送转历史。
    返回: [{date, bonus_rmb(每股派息), transfer_ratio(转增比例), bonus_ratio(送股比例)}]
    """
    data = eastmoney_datacenter('RPT_SHAREBONUS_DET', filter_str=f'(SECURITY_CODE="{code}")', page_size=page_size, sort_columns='EX_DIVIDEND_DATE', sort_types='-1')
    rows = []
    for row in data:
        rows.append({'date': str(row.get('EX_DIVIDEND_DATE', ''))[:10], 'bonus_rmb': row.get('PRETAX_BONUS_RMB', 0), 'transfer_ratio': row.get('TRANSFER_RATIO', 0), 'bonus_ratio': row.get('BONUS_RATIO', 0), 'plan': row.get('ASSIGN_PROGRESS', '')})
    return rows
data = dividend_history('600519')
import requests

def stock_fund_flow_120d(code: str) -> list[dict]:
    """
    个股资金流（日级，最近120个交易日）。
    返回: [{date, main_net(主力净流入), small_net, mid_net, large_net, super_net}]
    单位: 元
    """
    market_code = em_market_code(code)
    url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {'secid': f'{market_code}.{code}', 'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65', 'lmt': '120'}
    headers = {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/', 'Origin': 'https://quote.eastmoney.com'}
    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        print(f'[WARN] push2 资金流请求失败: {e}')
        return []
    klines = (d.get('data') or {}).get('klines') or []
    rows = []
    for line in klines:
        parts = line.split(',')
        if len(parts) >= 7:
            rows.append({'date': parts[0], 'main_net': float(parts[1]) if parts[1] != '-' else 0, 'small_net': float(parts[2]) if parts[2] != '-' else 0, 'mid_net': float(parts[3]) if parts[3] != '-' else 0, 'large_net': float(parts[4]) if parts[4] != '-' else 0, 'super_net': float(parts[5]) if parts[5] != '-' else 0})
    return rows
data = stock_fund_flow_120d('600519')
recent_20 = data[-20:]
total_main = sum((d['main_net'] for d in recent_20))
import numpy as np
import pandas as pd

def _triangular_weights(grid: np.ndarray, low: float, high: float, avg: float) -> np.ndarray:
    """当日筹码在价格网格上的三角分布权重（峰值在均价，面积归一）"""
    w = np.zeros_like(grid)
    if not np.isfinite([low, high, avg]).all() or high < low:
        return w
    if high - low < 1e-09:
        w[np.argmin(np.abs(grid - low))] = 1.0
        return w
    avg = min(max(avg, low), high)
    left = (grid >= low) & (grid <= avg)
    right = (grid > avg) & (grid <= high)
    if avg - low > 1e-09:
        w[left] = (grid[left] - low) / (avg - low)
    else:
        w[left] = 1.0
    if high - avg > 1e-09:
        w[right] = (high - grid[right]) / (high - avg)
    else:
        w[right] = 1.0
    total = w.sum()
    if total > 0:
        return w / total
    w[np.argmin(np.abs(grid - avg))] = 1.0
    return w

def chip_distribution(df: pd.DataFrame, grid_size: int=300, decay: float=1.0) -> dict:
    """筹码分布 — df 需含 high/low/close/turn（turn 为百分数，0.31 表示 0.31%）

    decay: 换手衰减系数。1.0=按真实换手率换手；同花顺口径常用 1.5~2.0 加快历史筹码消散。
    """
    need = {'date', 'high', 'low', 'close', 'turn'}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f'chip_distribution 缺少列: {sorted(missing)}（date 用于强制时间升序）')
    d = df.dropna(subset=['high', 'low', 'close', 'turn']).copy()
    d = d[d['high'] > 0]
    if d.empty:
        raise ValueError('chip_distribution: 有效行数为 0（检查是否全是停牌日，或字段类型不对）')
    d = d.sort_values('date').reset_index(drop=True)
    lo, hi = (float(d['low'].min()), float(d['high'].max()))
    pad = (hi - lo) * 0.02 or max(lo * 0.02, 0.01)
    grid = np.linspace(lo - pad, hi + pad, grid_size)
    chips = None
    for row in d.itertuples(index=False):
        t = float(row.turn) / 100.0 * decay
        t = min(max(t, 0.0), 1.0)
        avg = (float(row.high) + float(row.low) + float(row.close)) / 3.0
        w = _triangular_weights(grid, float(row.low), float(row.high), avg)
        if w.sum() <= 0:
            continue
        if chips is None:
            chips = w.copy()
            continue
        chips = chips * (1.0 - t) + w * t
    if chips is None:
        raise RuntimeError('chip_distribution: 所有交易日的价格区间都无效，无法构建分布')
    total = chips.sum()
    if total <= 0:
        raise RuntimeError('chip_distribution: 筹码总量为 0，无法计算指标')
    chips = chips / total
    price = float(d['close'].iloc[-1])
    cum = np.cumsum(chips)

    def price_at(q: float) -> float:
        return float(np.interp(q, cum, grid))
    p05, p15, p85, p95 = (price_at(q) for q in (0.05, 0.15, 0.85, 0.95))
    peak_i = int(np.argmax(chips))
    return {'price': price, 'profit_ratio': float(chips[grid <= price].sum()), 'avg_cost': float((grid * chips).sum()), 'cost_90': (p05, p95), 'cost_70': (p15, p85), 'concentration_90': float((p95 - p05) / (p95 + p05)) if p95 + p05 else None, 'concentration_70': float((p85 - p15) / (p85 + p15)) if p85 + p15 else None, 'peak_price': float(grid[peak_i]), 'histogram': [(float(pp), float(cc)) for pp, cc in zip(grid, chips) if cc > 1e-06]}
import baostock as bs
bs_code = _bs_code('600519')
k = k[k['tradestatus'] == '1']
r = chip_distribution(k)
import re
import time
SSE_ETF_SHARES_URL = 'https://query.sse.com.cn/commonQuery.do'
SZSE_FUND_LIST_URL = 'https://fund.szse.cn/api/report/ShowReport/data'

def _etf_shares_sse(day):
    params = {'sqlId': 'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L', 'STAT_DATE': day, 'isPagination': 'true', 'pageHelp.pageSize': 10000, 'pageHelp.pageNo': 1, 'pageHelp.beginPage': 1, 'pageHelp.cacheSize': 1, 'pageHelp.endPage': 1}
    response = _v39_http(SSE_ETF_SHARES_URL, params=params, headers={'Referer': 'https://www.sse.com.cn/'})
    payload = _v39_json(response)
    result = payload.get('result') if isinstance(payload, dict) else None
    page_help = payload.get('pageHelp') if isinstance(payload, dict) else None
    if not isinstance(result, list) or not all((isinstance(r, dict) for r in result)) or (not isinstance(page_help, dict)):
        raise RuntimeError('上交所 ETF 规模接口返回结构改变')
    total = _v39_count(page_help.get('total'), '上交所 ETF 规模 pageHelp.total')
    if len(result) != total:
        raise RuntimeError(f'上交所 ETF 规模返回 {len(result)} 条，与自报总数 {total} 不符，结果不完整')
    if not result:
        raise ValueError(f'上交所 {day} 没有 ETF 份额数据：非交易日或尚未发布')
    rows = []
    for rec in result:
        if rec.get('STAT_DATE') != day:
            raise RuntimeError('上交所返回了其他日期的数据')
        try:
            rows.append({'date': day, 'exchange': 'SH', 'code': rec['SEC_CODE'], 'name': rec['SEC_NAME'], 'etf_type': rec.get('ETF_TYPE'), 'shares_10k': _v39_req_num(rec['TOT_VOL'], '上交所 ETF 份额')})
        except KeyError as exc:
            raise RuntimeError(f'上交所 ETF 规模字段缺失: {exc!r}') from exc
    return (rows, response.url)

def _etf_shares_szse(day):
    rows, page, first, url = ([], 1, None, SZSE_FUND_LIST_URL)
    while first is None or page <= first[1]:
        response = _v39_http(SZSE_FUND_LIST_URL, params={'SHOWTYPE': 'JSON', 'CATALOGID': '1000_lf', 'TABKEY': 'tab1', 'selectJjlb': 'ETF', 'PAGENO': page}, headers={'Referer': 'https://fund.szse.cn/'})
        payload = _v39_json(response)
        table = payload[0] if isinstance(payload, list) and payload else None
        meta = table.get('metadata') if isinstance(table, dict) else None
        data = table.get('data') if isinstance(table, dict) else None
        if not isinstance(meta, dict) or not isinstance(data, list):
            raise RuntimeError(f'深交所基金列表第 {page} 页的返回结构变了（缺 metadata / data）')
        snap = (_v39_src_date(meta.get('subname')), _v39_count(meta.get('pagecount'), '深交所基金列表 pagecount'), _v39_count(meta.get('recordcount'), '深交所基金列表 recordcount'))
        if snap[1] < 1:
            raise RuntimeError(f'深交所基金列表 pagecount={snap[1]}，分页信息异常')
        if first is None:
            if snap[0] != day:
                raise ValueError(f'深交所当前规模快照日期是 {snap[0]}，不是 {day}；深市只能取最新一天，历史份额请自行按日留存')
            first = snap
        elif snap != first:
            raise RuntimeError(f'深交所 ETF 列表翻页时快照从 {first} 变成 {snap}，请重试')
        if not data:
            raise RuntimeError(f'深交所 ETF 列表第 {page}/{first[1]} 页是空的，结果不完整')
        for rec in data:
            try:
                code = re.search('<u>(\\d{6})</u>', rec['sys_key'])
                name = re.search('<u>(.*?)</u>', rec['jjjcurl'])
                shares = re.search('>([\\d,\\.]+)</a>', rec['dqgm'])
            except (KeyError, TypeError) as exc:
                raise RuntimeError(f'深交所基金列表字段缺失: {exc!r}') from exc
            if not (code and name and shares):
                raise RuntimeError('深交所基金列表字段格式改变')
            rows.append({'date': day, 'exchange': 'SZ', 'code': code.group(1), 'name': name.group(1), 'fund_category': rec.get('tzlb'), 'shares_10k': _v39_req_num(shares.group(1), '深交所 ETF 份额'), 'manager': rec.get('glrmc'), 'listing_date': rec.get('ssrq')})
        url = response.url
        page += 1
        time.sleep(0.3)
    if len(rows) != first[2]:
        raise RuntimeError(f'深交所 ETF 列表取到 {len(rows)} 条，与它自报的总数 {first[2]} 不符')
    return (rows, url)

@_v39_contract
def etf_shares(date, exchange='SH'):
    """ETF 份额（万份）— 上交所按日归档，深交所只有当前快照。

    date: 'YYYY-MM-DD'。上交所可查历史日期（实测 2023-01-03 仍有 446 只）；
    深交所只返回最新一天，date 与快照日期不符直接抛错（深交所注明 T 日晚为预估、T+1 早为确认值）。
    exchange: 'SH' 或 'SZ'。两所单位都是万份。上交所的 etf_type 是单市/跨市/跨境等，
    深交所给的是 fund_category（股票基金/债券基金…），两者口径不同，没有混成一列。
    """
    day = _v39_date(date)
    exchange = str(exchange).upper()
    if exchange == 'SH':
        rows, url = _etf_shares_sse(day)
    elif exchange == 'SZ':
        rows, url = _etf_shares_szse(day)
    else:
        raise ValueError("exchange 只能是 'SH' 或 'SZ'")
    frame = _v39_frame(rows, exchange.lower() + 'se', url)
    if frame.duplicated(['code']).any():
        raise RuntimeError('ETF 份额数据代码重复，不能当成完整快照')
    return frame
sh = etf_shares('2026-09-18', 'SH')
sz = etf_shares('2026-09-18', 'SZ')
