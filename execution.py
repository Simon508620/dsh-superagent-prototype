# -*- coding: utf-8 -*-
"""
执行模块（本鱼负责的环节）
- 幂等：同一 code+date+action 只执行一次
- 失败重试 ≤3 次，仍失败标记 rejected
- 模拟撮合：默认按信号价成交；真实环境接券商 API，接口不变
"""
import itertools
import random
import time

_order_seq = itertools.count(1)
# 幂等登记：{(code, date, action): order_id}
_idempotency_registry: dict = {}


def _try_fill(signal: dict, attempt: int, rng: random.Random) -> tuple[bool, str | None]:
    """模拟一次下单尝试。90% 成功；失败原因模拟网络抖动。"""
    if rng.random() < 0.9:
        return True, None
    return False, f"第{attempt}次下单失败：模拟网络超时"


def place_order(signal: dict, account: dict, max_retries: int = 3, seed: int | None = None) -> dict:
    """接口契约见 对接流程.md 3.2。下单并返回订单状态。"""
    date = signal["timestamp"].split(" ")[0]
    key = (signal["code"], date, signal["action"])

    # 幂等检查
    if key in _idempotency_registry:
        return {
            "order_id": _idempotency_registry[key],
            "code": signal["code"],
            "side": signal["action"],
            "qty": 0,
            "status": "rejected",
            "filled_price": None,
            "reject_reason": "幂等拦截：同 code+date+action 已执行过",
        }

    rng = random.Random(f"{seed}-{key}") if seed is not None else random.Random(str(key))
    qty = 100  # 最小 1 手；真实环境按仓位公式计算
    last_err = None
    for attempt in range(1, max_retries + 1):
        ok, err = _try_fill(signal, attempt, rng)
        if ok:
            order_id = f"ORD{date.replace('-', '')}{next(_order_seq):04d}"
            _idempotency_registry[key] = order_id
            return {
                "order_id": order_id,
                "code": signal["code"],
                "side": signal["action"],
                "qty": qty,
                "status": "filled",
                "filled_price": signal["price"],
                "reject_reason": None,
            }
        last_err = err
        time.sleep(0.01)  # 模拟退避

    return {
        "order_id": None,
        "code": signal["code"],
        "side": signal["action"],
        "qty": qty,
        "status": "rejected",
        "filled_price": None,
        "reject_reason": f"重试{max_retries}次仍失败：{last_err}",
    }
