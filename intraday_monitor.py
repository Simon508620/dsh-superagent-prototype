# -*- coding: utf-8 -*-
"""
盘中实时信号模块（T1-T1：盘中实时信号）
在现有 strategy.py 5层基础上，加盘中行情轮询 + 盘中触发买入信号 + 落盘盘中信号记录。
铁律：盘中行情必须真实接口（腾讯 qt.gtimg.cn 实时快照 / 东财 push2 / 新浪），严禁模拟数据。
盘中与盘后差异（诚实缺省，绝不编造）：
  - 盘中实时价/涨幅/量/额：腾讯 qt.gtimg.cn 批量实时快照（_fetch_batch），盘中随时可拉 ✅
  - 盘中情绪（涨跌家数/涨停池/成交额）：东财 push2/push2ex 盘中可拉，限流时新浪兜底 ✅
  - 盘中板块：东财 clist 盘中可拉，限流时新浪兜底 ✅
  - 盘中席位/资金（龙虎榜/超大单）：龙虎榜盘后才有 → 盘中诚实缺省（第4层盘中使用已有或空）
  - 盘中催化/公告：东财 np-anotice-stock 盘中可拉 ✅
接口契约：poll_intraday(date, codes, interval_sec, max_rounds, out_dir) → 落盘每轮轮询 + 触发的买入信号。
"""
import os
import sys
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_source
import strategy


def _now_hhmm() -> str:
    return datetime.now().strftime("%H%M")


def _intraday_rows(codes: list[str]) -> list[dict]:
    """盘中拉真实实时行情（腾讯批量），返回标准行情行 list[dict]。
    失败抛异常（绝不模拟）；单只缺失跳过（诚实），全部缺失抛异常。"""
    batch = data_source._fetch_batch(codes)
    if not batch:
        raise RuntimeError("盘中实时行情全部拉取失败（腾讯 qt.gtimg.cn）")
    rows = []
    for code in codes:
        r = batch.get(code)
        if r:
            rows.append(r)
    return rows


def _intraday_seat_fund(codes: list[str]) -> dict:
    """盘中席位/资金：龙虎榜盘后才有，盘中诚实缺省（返回空 dict，第4层不验证席位）。
    绝不编造盘中席位数据。"""
    return {}


def _intraday_catalyst(codes: list[str]) -> dict:
    """盘中催化/公告：东财公告盘中可拉（真实），失败诚实缺省。"""
    out = {}
    for code in codes:
        try:
            out[code] = data_source.fetch_catalyst_and_auction(code)
        except Exception:
            out[code] = {}  # 诚实缺省
    return out


def _intraday_params(pool_codes: list[str]) -> dict:
    """盘中5层真实数据：情绪/板块盘中拉（限流新浪兜底），席位盘中缺省，催化盘中拉。"""
    sentiment = {}
    try:
        sentiment = data_source.fetch_sentiment()
    except Exception:
        sentiment = {}
    sectors = []
    try:
        sectors = data_source.fetch_sector_ranking()
    except Exception:
        sectors = []
    seat_fund = _intraday_seat_fund(pool_codes)
    catalyst = _intraday_catalyst(pool_codes)
    return {
        "sentiment": sentiment,
        "sectors": sectors,
        "seat_fund": seat_fund,
        "catalyst": catalyst,
    }


def poll_intraday(date: str, codes: list[str], interval_sec: int = 60,
                  max_rounds: int = 5, out_dir: str = "data") -> dict:
    """盘中轮询主入口。每轮：拉实时行情 → 5层信号 → 落盘轮询记录 + 触发信号。
    返回 {date, rounds: [...], triggered: [...], signals_dir}。铁律：全真实，严禁模拟。
    interval_sec 为轮询间隔（秒），max_rounds 为最大轮询次数（默认5次=5分钟样例）。
    """
    out_dir = os.path.join(out_dir, "intraday_signals")
    os.makedirs(out_dir, exist_ok=True)
    rounds = []
    triggered = []
    seen_codes = set()

    for r in range(1, max_rounds + 1):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            rows = _intraday_rows(codes)
        except Exception as e:
            rounds.append({"round": r, "ts": ts, "status": "error", "error": str(e)})
            print(f"[盘中轮询 {r}/{max_rounds}] {ts} 行情拉取失败（诚实）：{e}")
            time.sleep(interval_sec)
            continue

        try:
            params = _intraday_params(codes)
        except Exception as e:
            params = {}
            print(f"[盘中轮询 {r}/{max_rounds}] 5层数据拉取失败（诚实缺省）：{e}")

        signals = strategy.generate_signals(rows, params)
        buy_signals = [s for s in signals if s.get("action") == "buy" and s.get("code")]

        record = {
            "round": r,
            "ts": ts,
            "status": "ok" if rows else "empty",
            "quote_count": len(rows),
            "quotes": rows,                     # 本轮实时行情快照（真实）
            "sentiment_temp": (params.get("sentiment") or {}).get("temperature"),
            "sector_count": len(params.get("sectors") or []),
            "signals": signals,                  # 5层信号（含 buy/hold）
            "buy_count": len(buy_signals),
            "buy_signals": buy_signals,
        }
        rounds.append(record)

        # 触发买入信号落盘（去重：同 code 只记首次触发）
        for s in buy_signals:
            code = s["code"]
            if code not in seen_codes:
                seen_codes.add(code)
                s["triggered_at"] = ts
                s["round"] = r
                triggered.append(s)

        hhmm = _now_hhmm()
        fp = os.path.join(out_dir, f"{date}_{hhmm}_r{r}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2, default=str)
        print(f"[盘中轮询 {r}/{max_rounds}] {ts} 行情{len(rows)}只，买入候选{len(buy_signals)}条，"
              f"落盘 {os.path.relpath(fp)}")

        if r < max_rounds:
            time.sleep(interval_sec)

    # 汇总触发信号独立落盘
    summary = {
        "date": date,
        "triggered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "triggered": triggered,
        "rounds": rounds,
        "signals_dir": out_dir,
    }
    sp = os.path.join(out_dir, f"{date}_summary.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"[盘中汇总] 触发买入信号 {len(triggered)} 条，落盘 {os.path.relpath(sp)}")
    return summary


if __name__ == "__main__":
    # 用法：python intraday_monitor.py [date] [interval_sec] [max_rounds]
    d = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    iv = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    mr = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    # 候选池：读取当日超短候选池（真实），无则用 8 只行业代表池（真实）
    pool_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", f"super_short_pool_{d}.csv")
    codes = []
    if os.path.exists(pool_path):
        import csv as _csv
        with open(pool_path, "r", encoding="utf-8-sig") as f:
            codes = [r.get("code") for r in _csv.DictReader(f) if r.get("code")]
    if not codes:
        codes = data_source.DEFAULT_POOL  # 真实 8 只行业代表池（非模拟）
    print(f"[盘中实时信号] 日期={d}，候选{len(codes)}只，轮询间隔{iv}s，最大{ mr }轮")
    poll_intraday(d, codes, interval_sec=iv, max_rounds=mr)
