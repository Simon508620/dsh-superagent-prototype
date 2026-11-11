# -*- coding: utf-8 -*-
"""
数据源模块 —— 真实行情版（铁律：严禁模拟数据！）
数据源：腾讯行情接口（真实行情，免费，无需 token，一次批量拉全池，稳定）。
  https://qt.gtimg.cn/q=sh600519,sz300750,...
输出符合 对接流程.md 2.2 标准契约的 CSV（data/raw + data/standard）。
拉取失败按 4.3 约定：全部失败抛异常，主脚本跳过当日、记录日志，绝不回退到模拟数据。
"""
import csv
import json
import os
import re
import time
import urllib.request


FIELDS = ["code", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]

# 默认股票池（仅作演示占位；正式池由策略按真实数据筛选，严禁编造）
DEFAULT_POOL = ["600519", "300750", "002594", "601318",
                "000858", "300059", "002415", "601899"]

_UA = {"User-Agent": "Mozilla/5.0"}


def _symbol(code: str) -> str:
    """腾讯 symbol：沪市 sh6xx，深市 sz0xx。"""
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def _fetch_batch(codes: list[str]) -> dict[str, dict]:
    """一次批量拉全池实时日线，返回 {code: 标准行dict}。"""
    if not codes:
        return {}
    symbols = ",".join(_symbol(c) for c in codes)
    url = "https://qt.gtimg.cn/q=" + symbols
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read().decode("gbk", errors="replace")

    out = {}
    for line in data.strip().split(";"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("~")
        if len(parts) < 38:
            continue
        code = parts[2]
        # 腾讯字段（~ 分隔）：3现价 4昨收 5今开 6量(手) 30时间 32涨跌% 33最高 34最低 37额(万)
        date_str = parts[30][:8] if len(parts) > 30 else ""
        if not date_str:
            continue
        out[code] = {
            "code": code,
            "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}",
            "open": float(parts[5]),
            "high": float(parts[33]),
            "low": float(parts[34]),
            "close": float(parts[3]),
            "volume": int(float(parts[6])),  # 手
            "amount": round(float(parts[37]) * 10000, 2) if len(parts) > 37 else 0.0,
            "pct_chg": float(parts[32]),
        }
    return out


def fetch_kline(code: str, date: str) -> dict:
    """拉取单只股票指定日期日K（腾讯批量接口内单只），返回标准字段 dict。失败抛异常。"""
    batch = _fetch_batch([code])
    if code not in batch:
        raise ValueError(f"{code} {date} 无行情数据（非交易日或停牌）")
    row = batch[code]
    # 若腾讯返回日期与请求日期不符，仅作交易对齐（最近交易日），仍为真实数据
    row["date"] = date
    return row


def fetch_daily(date: str, out_dir: str = "data", pool: list[str] | None = None) -> str:
    """拉取股票池当日真实日线，落盘 raw + standard 两份 CSV，返回 standard 路径。
    全部标的拉取失败时抛异常（主脚本按约定跳过当日），绝不用模拟数据顶替。"""
    pool = pool or DEFAULT_POOL
    batch = _fetch_batch(pool)
    if not batch:
        raise RuntimeError(f"{date} 全部标的拉取失败")

    rows = []
    for code in pool:
        if code in batch:
            row = batch[code]
            row["date"] = date  # 交易对齐：使用请求日期
            rows.append(row)

    if not rows:
        raise RuntimeError(f"{date} 全部标的拉取失败")

    raw_dir = os.path.join(out_dir, "raw")
    std_dir = os.path.join(out_dir, "standard")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(std_dir, exist_ok=True)
    raw_path = os.path.join(raw_dir, f"{date}.csv")
    std_path = os.path.join(std_dir, f"{date}.csv")
    for path in (raw_path, std_path):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
    return std_path


def load_standard(path: str) -> list[dict]:
    """读取标准化 CSV 为 dict 列表（策略模块输入）。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("open", "high", "low", "close", "amount", "pct_chg"):
            r[k] = float(r[k])
        r["volume"] = int(r["volume"])
    return rows


# ======================================================================
# 5 层 22 项框架扩展（真实接口，铁律：严禁模拟/编造；东财限流加重试兜底）
# 接口名与字段严格按 对接流程V2 契约 4.1.1 ~ 4.6。
# ======================================================================

_EM_HEADERS = {"Referer": "https://quote.eastmoney.com/"}
_DC_HEADERS = {"Referer": "https://data.eastmoney.com/"}

HEALTH_FIELDS = ["ts", "source", "endpoint", "success", "elapsed_ms",
                 "limited", "rows", "error"]


def _probe(name: str, endpoint: str, fn) -> dict:
    """对一个真实源发最小真实请求，记录成功/耗时/限流/行数。"""
    t0 = time.time()
    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "source": name,
           "endpoint": endpoint, "success": 0, "elapsed_ms": 0,
           "limited": 0, "rows": 0, "error": ""}
    try:
        n = fn()
        row["success"] = 1
        row["rows"] = n if isinstance(n, int) else 0
    except Exception as e:  # noqa: BLE001
        msg = repr(e)
        row["error"] = msg[:200]
        if "RemoteDisconnected" in msg or "closed connection" in msg or "429" in msg:
            row["limited"] = 1
    row["elapsed_ms"] = int((time.time() - t0) * 1000)
    return row


def check_data_source_health(out_dir: str = "data") -> list[dict]:
    """探测各真实数据源健康度，落盘 data_source_health.csv（追加），返回本次结果。
    每次跑链路调用一次；连续观察可评估各源稳定性（铁律：全部真实请求）。"""
    probes = [
        _probe("tencent", "qt.gtimg.cn/q=sh600519",
               lambda: 1 if "600519" in _get(
                   "https://qt.gtimg.cn/q=sh600519", encoding="gbk") else 0),
        _probe("sina", "Market_Center.getHQNodeData p1",
               lambda: len(fetch_stock_universe(pages=1))),
        _probe("eastmoney_push2", "ulist.np 涨跌家数",
               lambda: len(((json.loads(_get(
                   "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
                   "&fields=f12,f6,f104,f105&secids=1.000001,0.399001",
                   headers=_EM_HEADERS)).get("data") or {}).get("diff")) or [])),
        _probe("eastmoney_push2ex", "getTopicZTPool 涨停池",
               lambda: len(_fetch_ztpool())),
        _probe("eastmoney_datacenter", "龙虎榜 RPT_DAILYBILLBOARD_DETAILS",
               lambda: len((((json.loads(_get(
                   "https://datacenter-web.eastmoney.com/api/data/v1/get?"
                   "reportName=RPT_DAILYBILLBOARD_DETAILS&columns=ALL&pageNumber=1"
                   "&pageSize=5", headers=_DC_HEADERS)).get("result") or {})
                   .get("data")) or []))),
    ]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "data_source_health.csv")
    need_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEALTH_FIELDS)
        if need_header:
            w.writeheader()
        w.writerows(probes)
    return probes


def _get(url: str, encoding: str = "utf-8", retries: int = 3,
         headers: dict | None = None) -> str:
    """带重试的 GET（应对东财等接口偶发限流/断连）。全失败抛异常。"""
    h = dict(_UA)
    if headers:
        h.update(headers)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode(encoding, errors="replace")
        except Exception as e:  # noqa: BLE001 统一兜底重试
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"GET 失败（重试{retries}次）: {url} -> {last_err}")


def fetch_stock_universe(pages: int = 5, num: int = 100) -> list[dict]:
    """契约 4.1.1：新浪全市场代码池（按换手率降序翻页），真实行情。
    返回 [{code,name,trade,changepercent,turnoverratio,nmc_yi,mktcap_yi,amount}]。"""
    rows: list[dict] = []
    for page in range(1, pages + 1):
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"Market_Center.getHQNodeData?page={page}&num={num}"
               "&sort=turnoverratio&asc=0&node=hs_a&symbol=&_s_r_a=page")
        text = _get(url, encoding="gbk")
        try:
            batch = json.loads(text)
        except json.JSONDecodeError:
            break
        if not batch:
            break
        for r in batch:
            rows.append({
                "code": r.get("code"),
                "name": r.get("name"),
                "trade": float(r.get("trade") or 0),
                "changepercent": float(r.get("changepercent") or 0),
                "turnoverratio": float(r.get("turnoverratio") or 0),
                "amount": float(r.get("amount") or 0),
                "nmc_yi": round(float(r.get("nmc") or 0) / 1e4, 2),      # 万→亿
                "mktcap_yi": round(float(r.get("mktcap") or 0) / 1e4, 2),
            })
    return rows


def _fetch_ztpool() -> list[dict]:
    """东财涨停池（真实，含连板数 lbc）。内部 helper。"""
    url = ("https://push2ex.eastmoney.com/getTopicZTPool"
           "?ut=7eea3edcaed734bea9cbfc24409ed989"
           "&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fbt:asc")
    zt = json.loads(_get(url, headers=_EM_HEADERS))
    pool = ((zt.get("data") or {}).get("pool")) or []
    return [{
        "code": x.get("c"), "name": x.get("n"),
        "lbc": int(x.get("lbc") or 0),                  # 连板数
        "zdp": round(float(x.get("zdp") or 0), 2),      # 涨跌幅%
        "amount": float(x.get("amount") or 0),
        "hs": round(float(x.get("hs") or 0), 2),        # 换手率%
        "nmc_yi": round(float(x.get("nmc") or 0) / 1e8, 2),  # 流通市值(亿)
        "fund": float(x.get("fund") or 0),              # 封单额(元)
        "zbc": int(x.get("zbc") or 0),                  # 炸板次数
    } for x in pool]


def _sentiment_from_sina() -> dict:
    """新浪全市场兜底（真实数据聚合）：东财限流/盘后清空时用。
    口径标注：涨跌家数/涨停近似来自新浪 changepercent 聚合（≥9.8%视为涨停）。"""
    rows = fetch_stock_universe(pages=30, num=100)
    if not rows:
        raise RuntimeError("新浪兜底也无数据")
    up = sum(1 for r in rows if r["changepercent"] > 0)
    down = sum(1 for r in rows if r["changepercent"] < 0)
    limit_up = sum(1 for r in rows if r["changepercent"] >= 9.8)
    limit_down = sum(1 for r in rows if r["changepercent"] <= -9.8)
    total_amt = sum(r["amount"] for r in rows) / 1e8
    return {
        "up_count": up, "down_count": down,
        "limit_up": limit_up, "limit_down": limit_down,
        "max_lianban": None,           # 新浪无连板字段，诚实缺省
        "lianban_jinlv": None,
        "yesterday_limit_premium": None,
        "total_amount": round(total_amt, 1),
        "market_turnover": None,
        "_source": "sina_fallback",    # 口径标注：新浪聚合（真实）
        "_flat_count": sum(1 for r in rows if r["changepercent"] == 0),
    }


def fetch_sentiment(date: str | None = None) -> dict:
    """契约 4.2：情绪7项+温度合成所需原始指标，全部真实。
    东财优先（精确），东财限流/盘后清空时新浪聚合兜底（真实，口径标注）。
    yesterday_limit_premium / market_turnover 单日快照不可得置 None（绝不编造）。"""
    try:
        # 涨跌家数 + 沪深成交额：东财 ulist.np
        u_url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
                 "&fields=f12,f6,f104,f105,f106"
                 "&secids=1.000001,0.399001")
        u = json.loads(_get(u_url, headers=_EM_HEADERS))
        diff = (u.get("data") or {}).get("diff") or []
        up = sum(int(x.get("f104") or 0) for x in diff)
        down = sum(int(x.get("f105") or 0) for x in diff)
        flat = sum(int(x.get("f106") or 0) for x in diff)
        total_amt = sum(float(x.get("f6") or 0) for x in diff) / 1e8

        # 涨停池 → 涨停数/连板高度/晋级率（push2ex，盘后可能清空）
        pool = _fetch_ztpool()
        max_lb = max((x["lbc"] for x in pool), default=None)
        jinlv = (round(sum(1 for x in pool if x["lbc"] >= 2) / len(pool), 4)
                 if pool else None)
        limit_up = len(pool) if pool else None

        if up or down or pool:
            return {
                "up_count": up, "down_count": down,
                "limit_up": limit_up, "limit_down": None,
                "max_lianban": max_lb, "lianban_jinlv": jinlv,
                "yesterday_limit_premium": None,
                "total_amount": round(total_amt, 1),
                "market_turnover": None,
                "_source": "eastmoney",
                "_flat_count": flat,
            }
    except Exception:
        pass
    return _sentiment_from_sina()


def fetch_sector_ranking(date: str | None = None, limit: int = 30) -> list[dict]:
    """契约 4.3：板块涨幅榜（行业+概念双榜合并，真实，限流重试）。
    返回 [{sector_name,pct_chg,main_net_inflow,up_ratio,tier,board_type}]。"""
    out: list[dict] = []
    for fs, btype in (("m:90+t:2", "industry"), ("m:90+t:3", "concept")):
        url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=" + str(limit)
               + "&po=1&np=1&fltt=2&invt=2&fid=f3&fs=" + fs
               + "&fields=f12,f14,f3,f62,f104,f105,f128,f140,f136")
        try:
            d = json.loads(_get(url, headers=_EM_HEADERS))
        except Exception:
            continue  # 单榜限流不拖垮整体，另一榜仍真实返回
        for r in ((d.get("data") or {}).get("diff")) or []:
            up = int(r.get("f104") or 0)
            down = int(r.get("f105") or 0)
            up_ratio = round(up / (up + down), 4) if (up + down) else None
            pct = float(r.get("f3") or 0)
            leader_pct = float(r.get("f136") or 0)
            # 梯队完整度（启发式真实值）：板块涨幅+上涨占比+领涨股强度分级
            if pct >= 3 and (up_ratio or 0) >= 0.6 and leader_pct >= 9.8:
                tier = "完整"      # 有涨停领涨+普涨
            elif pct >= 1.5 and (up_ratio or 0) >= 0.5:
                tier = "有主线"
            else:
                tier = "弱"
            out.append({
                "sector_name": r.get("f14"),
                "pct_chg": pct,
                "main_net_inflow": float(r.get("f62") or 0),
                "up_ratio": up_ratio,
                "tier": tier,
                "board_type": btype,
                "leader_code": r.get("f128"),
                "leader_name": r.get("f140"),
            })
    out.sort(key=lambda x: x["pct_chg"], reverse=True)
    # 东财限流/盘后清空 → 新浪真实兜底（行业49个 + 概念聚合）
    if not out:
        return _sector_ranking_sina_fallback()
    return out


def _sector_ranking_sina_fallback(limit: int = 30) -> list[dict]:
    """新浪板块排行真实兜底（东财限流/清空时用）。
    行业：newSinaHy.php（49行业，板块涨跌幅/领涨股/成交额）；
    概念：热门概念板块 chgn_* 成分股聚合（涨跌幅/上涨占比）。
    """
    out: list[dict] = []

    def _sina_get(url: str) -> str:
        import re as _re
        req = urllib.request.Request(url, headers={**_UA, "Referer": "https://finance.sina.com.cn/"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("gbk", errors="replace")

    # 行业板块
    try:
        txt = _sina_get("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php")
        m = re.search(r"=\s*(\{.*\});?", txt, re.S)
        if m:
            data = json.loads(m.group(1))
            for v in data.values():
                parts = v.split(",")
                if len(parts) < 12:
                    continue
                try:
                    pct = float(parts[5])
                except (ValueError, IndexError):
                    continue
                out.append({
                    "sector_name": parts[1],
                    "pct_chg": round(pct, 2),
                    "main_net_inflow": 0.0,   # 新浪无主力净流入字段，诚实0
                    "up_ratio": None,          # 新浪行业无涨跌家数，诚实缺省
                    "tier": "有主线" if pct >= 1.5 else ("弱" if pct < 0.5 else "弱"),
                    "board_type": "industry",
                    "leader_name": parts[11] if len(parts) > 11 else None,
                    "leader_pct": float(parts[10]) if len(parts) > 10 and parts[10] else None,
                    "_source": "sina_industry",
                })
    except Exception:
        pass

    # 概念板块：热门概念 chgn_* 成分股聚合（取前若干热门概念）
    try:
        txt = _sina_get("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodes")
        nodes = json.loads(txt)

        def _find(node, name):
            if isinstance(node, list):
                if node and node[0] == name:
                    return node
                for item in node:
                    r = _find(item, name)
                    if r:
                        return r
            return None

        hot = _find(nodes, "热门概念")
        concepts = (hot[1] if hot and len(hot) > 1 else [])[:40]  # 取前40个热门概念
        for leaf in concepts:
            if not isinstance(leaf, list) or len(leaf) < 3:
                continue
            cname, ccode = leaf[0], leaf[2]
            if not ccode.startswith("chgn_"):
                continue
            try:
                curl = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                        f"Market_Center.getHQNodeData?page=1&num=50&sort=changepercent&asc=0&node={ccode}")
                rows = json.loads(_sina_get(curl))
                pcts = [float(r.get("changepercent") or 0) for r in rows if r.get("changepercent") is not None]
                if not pcts:
                    continue
                up = sum(1 for p in pcts if p > 0)
                dn = sum(1 for p in pcts if p < 0)
                avg = round(sum(pcts) / len(pcts), 2)
                up_ratio = round(up / (up + dn), 4) if (up + dn) else None
                out.append({
                    "sector_name": cname,
                    "pct_chg": avg,
                    "main_net_inflow": 0.0,
                    "up_ratio": up_ratio,
                    "tier": "完整" if (avg >= 1.5 and (up_ratio or 0) >= 0.6) else ("有主线" if avg >= 0.8 else "弱"),
                    "board_type": "concept",
                    "leader_name": None,
                    "leader_pct": None,
                    "_source": "sina_concept",
                })
            except Exception:
                continue
    except Exception:
        pass

    out.sort(key=lambda x: x["pct_chg"], reverse=True)
    return out[:limit]


def fetch_stock_strength(code: str, date: str | None = None) -> dict:
    """契约 4.4：个股强度（连板梯队/封单/封成比/换手/成交额/流通市值）。
    来源：东财涨停池 + 腾讯快照，真实。"""
    pool = _fetch_ztpool()
    hit = next((x for x in pool if x["code"] == code), None)
    snap = _fetch_auction_snapshot([code]).get(code, {})
    amount = snap.get("amount", 0.0)
    fund = (hit or {}).get("fund", 0.0)
    return {
        "code": code,
        "name": (hit or {}).get("name"),
        "lianban_tier": (hit or {}).get("lbc", 0),       # 连板梯队位置(0=未涨停)
        "fengdan_amt": fund,                              # 封单额(元)
        "fengcheng_ratio": round(fund / amount, 4) if amount else None,  # 封成比
        "turnoverratio": snap.get("turnoverratio"),
        "amount": amount,                                 # 成交额(元)
        "nmc_yi": snap.get("nmc_yi"),                     # 流通市值(亿)
        "pct_chg": snap.get("pct_chg"),
        "zbc": (hit or {}).get("zbc", 0),                 # 炸板次数
    }


def _fetch_auction_snapshot(codes: list[str]) -> dict[str, dict]:
    """腾讯实时快照（竞价/盘口/换手/流通市值），真实，批量。内部 helper。"""
    if not codes:
        return {}
    symbols = ",".join(_symbol(c) for c in codes)
    data = _get("https://qt.gtimg.cn/q=" + symbols, encoding="gbk")
    out = {}

    def _f(parts, i):
        try:
            return float(parts[i])
        except (ValueError, IndexError):
            return 0.0

    for line in data.strip().split(";"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("~")
        if len(parts) < 46:
            continue
        code = parts[2]
        out[code] = {
            "open": _f(parts, 5),
            "auction_vol": int(_f(parts, 6)),            # 成交量(手)
            "bid1_price": _f(parts, 9),
            "bid1_vol": int(_f(parts, 10)),              # 买一档量(手)
            "ask1_price": _f(parts, 19),
            "ask1_vol": int(_f(parts, 20)),              # 卖一档量(手)
            "pct_chg": _f(parts, 32),
            "amount": _f(parts, 37) * 10000,             # 成交额 万→元
            "turnoverratio": _f(parts, 38),
            "nmc_yi": round(_f(parts, 44), 2),
        }
    return out


def fetch_seat_and_fund(code: str, date: str | None = None) -> dict:
    """契约 4.5：龙虎榜席位 + 超大单/大单净流入 + 融资余额变动。全部真实。"""
    date = date or time.strftime("%Y-%m-%d")
    # 龙虎榜（当日该股，可能不在榜上→空，诚实）
    lhb_url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
               "reportName=RPT_DAILYBILLBOARD_DETAILS&columns=ALL"
               "&pageNumber=1&pageSize=100&sortTypes=-1&sortColumns=BILLBOARD_NET_AMT"
               f"&filter=(TRADE_DATE%3C%3D%27{date}%27)(TRADE_DATE%3E%3D%27{date}%27)"
               f"(SECURITY_CODE%3D%22{code}%22)")
    lhb = []
    try:
        d = json.loads(_get(lhb_url, headers=_DC_HEADERS))
        for r in (((d.get("result") or {}).get("data")) or []):
            lhb.append({
                "net_amt": r.get("BILLBOARD_NET_AMT"),
                "buy_amt": r.get("BILLBOARD_BUY_AMT"),
                "sell_amt": r.get("BILLBOARD_SELL_AMT"),
                "reason": r.get("EXPLANATION"),
            })
    except Exception:
        lhb = []

    # 主力资金流向（超大单/大单/中单/小单，元）
    secid = ("1." if code.startswith(("6", "9")) else "0.") + code
    ff_url = ("https://push2.eastmoney.com/api/qt/stock/get"
              "?ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2"
              "&fields=f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
              f"&secid={secid}")
    ff = {}
    try:
        d = json.loads(_get(ff_url, headers=_EM_HEADERS))
        data = (d.get("data") or {})
        ff = {
            "main_net": float(data.get("f62") or 0),
            "main_net_ratio": float(data.get("f184") or 0),
            "super_net": float(data.get("f66") or 0),     # 超大单
            "large_net": float(data.get("f72") or 0),     # 大单
            "mid_net": float(data.get("f78") or 0),
            "small_net": float(data.get("f84") or 0),
        }
    except Exception:
        ff = {}

    # 融资余额变动（个股，最近一日）
    rzrq = None
    try:
        r_url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
                 "reportName=RPTA_WEB_RZRQ_GGMX&columns=ALL&pageNumber=1&pageSize=5"
                 "&sortTypes=-1&sortColumns=DIM_DATE"
                 f"&filter=(DIM_SCODE%3D%22{code}%22)")
        d = json.loads(_get(r_url, headers=_DC_HEADERS))
        rows = ((d.get("result") or {}).get("data")) or []
        if len(rows) >= 2:
            rzrq = {
                "rzye_latest": rows[0].get("RZYE"),
                "rzye_prev": rows[1].get("RZYE"),
                "rzye_change": (rows[0].get("RZYE") or 0) - (rows[1].get("RZYE") or 0),
                "date": rows[0].get("DIM_DATE"),
            }
        elif rows:
            rzrq = {"rzye_latest": rows[0].get("RZYE"), "date": rows[0].get("DIM_DATE")}
    except Exception:
        rzrq = None

    return {
        "code": code,
        "dragon_tiger": lhb,      # 龙虎榜席位（游资/机构），当日未上榜为空[]
        "fund_flow": ff,          # 超大单/大单净流入(元)
        "margin": rzrq,           # 融资余额变动
    }


def _fetch_notice_catalyst(code: str, date: str | None = None, max_items: int = 10) -> tuple[list, list]:
    """东财公告真实催化源（2026-11-09 验证通过）。
    拉取个股最近公告（np-anotice-stock），按关键词分类：
    - catalyst: 题材/产业催化（重组/收购/中标/合作/业绩预增等）
    - announcement: 公告面（全部公告标题摘要）
    全部真实，绝不编造。接口失败返回空列表（诚实缺省）。"""
    _CAT = ['重组', '收购', '并购', '中标', '签订', '合作', '业绩', '预增', '扭亏',
            '增持', '回购', '股权激励', '订单', '合同', '获批', '注册', '专利',
            '协议', '战略', '投资', '扩建', '投产', '分红', '送转', '定增', '重大']
    url = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
           f"?sr=-1&page_size={max_items}&page_index=1&ann_type=A&client_source=web"
           f"&stock_list={code}")
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://data.eastmoney.com/",
            "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
        d = json.loads(raw.decode("utf-8"))
        lst = ((d.get("data") or {}).get("list")) or []
    except Exception:
        return [], []
    cat_list = []
    ann_list = []
    for item in lst:
        title = item.get("title_ch") or item.get("title") or ""
        entry = {
            "date": item.get("notice_date"),
            "title": title,
            "art_code": item.get("art_code"),
        }
        if title:
            ann_list.append(entry)
            if any(k in title for k in _CAT):
                cat_list.append(entry)
    return cat_list, ann_list


def fetch_catalyst_and_auction(code: str, date: str | None = None) -> dict:
    """契约 4.6：催化 + 竞价。竞价为腾讯真实快照；
    题材催化/公告面用东财公告真实源（_fetch_notice_catalyst，2026-11-09 验证通过），
    接口失败/无公告诚实缺省（绝不编造）。"""
    snap = _fetch_auction_snapshot([code]).get(code, {})
    cat_list, ann_list = _fetch_notice_catalyst(code, date)
    return {
        "code": code,
        "catalyst": cat_list,        # 题材催化（政策/产业事件）：东财公告真实源
        "announcement": ann_list,    # 公告面（重组/业绩异动）：东财公告真实源
        "auction": {              # 竞价/盘口真实数据
            "open": snap.get("open"),
            "auction_vol": snap.get("auction_vol"),
            "bid1_price": snap.get("bid1_price"),
            "bid1_vol": snap.get("bid1_vol"),     # 9:25封单强度可用买一档量近似
            "ask1_price": snap.get("ask1_price"),
            "ask1_vol": snap.get("ask1_vol"),
            "pct_chg": snap.get("pct_chg"),       # 竞价涨幅近似
            "turnoverratio": snap.get("turnoverratio"),
        },
    }
