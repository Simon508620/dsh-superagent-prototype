# -*- coding: utf-8 -*-
"""
S4 每日编排脚本 —— 真实样本积累（阶段4 · Agent编排交付）
每日盘后跑一次：筛当日候选池 → 跑全链路 → 落盘样本 → 数据源健康度。

用法：python run_daily_s4.py [YYYY-MM-DD]
  - 无日期参数：用今天（A股交易日，非交易日会诚实跳过）
铁律：全部真实行情（新浪/腾讯/东财），严禁模拟；数据不可得诚实缺省/空仓。

输出：
  - data/super_short_pool_{date}.csv      当日真实候选池
  - data/samples/sample_{date}.json       当日完整样本（信号/评分/作战计划/执行/风控/复盘）
  - data/review_{date}.csv                当日复盘摘要
  - data/data_source_health.csv           数据源健康度（追加）
"""
import json
import os
import sys
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_source
import filter_super_short_pool
import review
import run_trading_workflow


def _load_positions_pnl(trade_date: str, data_dir: str) -> dict:
    """读当日持仓文件 + 腾讯真实行情，算每只浮亏/涨跌幅/是否破-5%止损。
    铁律：全真实行情（腾讯 qt.gtimg.cn），无 positions 文件返回空 dict；行情不可得诚实置 None。
    返回 {code: {name,qty,cost_price,current_price,pct_chg,pnl_pct,stop_loss_hit}}。
    """
    pos_path = os.path.join(data_dir, f"positions_{trade_date}.json")
    if not os.path.exists(pos_path):
        return {}
    try:
        with open(pos_path, "r", encoding="utf-8") as f:
            positions = json.load(f)
    except Exception as e:
        print(f"[positions_pnl] 持仓文件读取失败（诚实跳过）：{e}")
        return {}
    import urllib.request
    pnl = {}
    for code, pos in positions.items():
        prefix = "sh" if code.startswith(("6", "5")) else "sz"
        tcode = prefix + code
        try:
            url = "https://qt.gtimg.cn/q=" + tcode
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="replace")
            f = raw.split("=", 1)[1].strip().strip('"').split("~")
            price = float(f[3]); prev = float(f[4]); pct = float(f[32])
            cost = pos.get("cost_price")
            pnl_pct = round((price - cost) / cost * 100, 2) if cost else None
            pnl[code] = {
                "name": pos.get("name", f[1]), "qty": pos.get("qty"),
                "cost_price": cost, "current_price": price,
                "prev_close": prev, "pct_chg": pct,
                "pnl_pct": pnl_pct,
                "stop_loss_hit": (pnl_pct is not None and pnl_pct <= -5.0),
            }
        except Exception as e:
            pnl[code] = {"name": pos.get("name"), "qty": pos.get("qty"),
                         "cost_price": pos.get("cost_price"), "current_price": None,
                         "pct_chg": None, "pnl_pct": None, "stop_loss_hit": False,
                         "error": repr(e)}
    return pnl


def _load_market_snapshot(trade_date: str) -> dict:
    """拉当日市场情绪 + 板块排行（真实数据，铁律），包装成样本 market_snapshot 字段。
    情绪：data_source.fetch_sentiment()（东财优先/新浪兜底，真实）；
    板块：data_source.fetch_sector_ranking(limit=10)（行业+概念双榜，真实）。
    数据不可得时诚实置空/None，绝不编造。"""
    try:
        sentiment = data_source.fetch_sentiment(trade_date)
    except Exception as e:
        sentiment = {"error": repr(e)}
    try:
        sectors = data_source.fetch_sector_ranking(trade_date, limit=10)
    except Exception as e:
        sectors = [{"error": repr(e)}]
    # 情绪温度（本鱼口径：涨/(涨+跌)×100，缺涨跌家数时 None）
    up = sentiment.get("up_count")
    down = sentiment.get("down_count")
    temp = round(up / (up + down) * 100, 1) if (up is not None and down is not None and (up + down)) else None
    return {
        "sentiment": sentiment,
        "sentiment_temperature": temp,
        "top_sectors": sectors[:10],
        "_source": "eastmoney_primary_sina_fallback",
    }


def main(trade_date: str | None = None):
    if trade_date is None:
        trade_date = _date.today().isoformat()
    base = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, "data")
    samples_dir = os.path.join(data_dir, "samples")
    os.makedirs(samples_dir, exist_ok=True)
    print(f"===== S4 每日编排 · {trade_date} =====")

    # 0. 数据源健康度（真实探测，铁律）
    try:
        health = data_source.check_data_source_health(out_dir=data_dir)
        ok_src = [h["source"] for h in health if h.get("success")]
        print(f"[0] 数据源健康度：{len(ok_src)}/{len(health)} 源正常 → {','.join(ok_src)}")
    except Exception as e:
        health = []
        print(f"[0] 数据源健康度探测异常（诚实记录）：{e}")

    # 1. 筛当日候选池（真实行情，铁律：非模拟）
    pool_csv = os.path.join(data_dir, f"super_short_pool_{trade_date}.csv")
    if not os.path.exists(pool_csv):
        print(f"[1] 当日候选池不存在，先筛池（{trade_date}）...")
        try:
            filter_super_short_pool.main(trade_date)
        except Exception as e:
            print(f"[1] 筛池失败（诚实记录，非模拟）：{e}")
    else:
        print(f"[1] 候选池已存在：{pool_csv}")

    # 2. 跑全链路（主脚本 V2.1，真实数据）
    print("[2] 跑全链路 run_trading_workflow ...")
    try:
        result = run_trading_workflow.run(trade_date)
        print(f"[2] 主脚本返回 status={result.get('status')}")
    except Exception as e:
        result = {"date": trade_date, "status": "error", "reason": repr(e)}
        print(f"[2] 主脚本异常（诚实记录）：{e}")

    # 3. 落盘当日样本（signal/plan/orders/portfolio/health 全量，JSON）
    sample_path = os.path.join(samples_dir, f"sample_{trade_date}.json")
    sample = {
        "date": trade_date,
        "status": result.get("status"),
        "signals": result.get("signals", []),
        "scored": result.get("scored", []),
        "plan": result.get("plan", {}),
        "orders": result.get("orders", []),
        "portfolio": result.get("portfolio", {}),
        "data_source_health": health,
        "reason": result.get("reason", result.get("reason", "")),
        "positions_pnl": _load_positions_pnl(trade_date, data_dir),
        "market_snapshot": _load_market_snapshot(trade_date),
    }
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2, default=str)
    print(f"[3] 样本落盘：{sample_path}")

    # 3b. 深度复盘（T2：逐笔盈亏归因 + 信号质量回测，读全部真实样本）
    print("[3b] 深度复盘（逐笔归因 + 信号质量回测）...")
    try:
        samples = []
        for fn in sorted(os.listdir(samples_dir)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(samples_dir, fn), "r", encoding="utf-8") as f:
                        samples.append(json.load(f))
                except Exception:
                    continue
        review.deep_review(samples, out_dir=data_dir)
    except Exception as e:
        print(f"[3b] 深度复盘异常（诚实记录）：{e}")

    print(f"===== S4 每日编排完成：status={result.get('status')} =====")
    return {"date": trade_date, "status": result.get("status"), "sample": sample_path}


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
