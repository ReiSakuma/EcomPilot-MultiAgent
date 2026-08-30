from __future__ import annotations


MAX_LAUNCH_DISCOUNT = 20.0


def calculate_margin(
    price: float,
    cost: float,
    discount_amount_yuan: float = 0,
    *,
    discount: float | None = None,
) -> dict[str, float | str]:
    """Calculate governed margin using an explicitly yuan-denominated reduction.

    ``discount`` remains a direct-call compatibility alias for pre-v56 callers. The
    registry Schema only exposes ``discount_amount_yuan`` to models.
    """

    if discount is not None:
        if discount_amount_yuan not in {0, float(discount)}:
            raise ValueError("discount and discount_amount_yuan conflict")
        discount_amount_yuan = float(discount)
    net_price = price - discount_amount_yuan
    margin = net_price - cost
    margin_rate = margin / net_price if net_price > 0 else -1
    return {
        "promotion_protocol_version": "1.0",
        "price": price,
        "discount_amount_yuan": discount_amount_yuan,
        # Deprecated compatibility projection. New model/tool paths use the field above.
        "discount": discount_amount_yuan,
        "net_price": net_price,
        "cost": cost,
        "margin": round(margin, 2),
        "margin_rate": round(margin_rate, 4),
    }


def maximum_safe_discount(
    price: float,
    cost: float,
    min_margin_rate: float,
    *,
    policy_cap: float = MAX_LAUNCH_DISCOUNT,
) -> float:
    margin_limited_discount = price - (cost / (1 - min_margin_rate))
    return max(0.0, round(min(policy_cap, margin_limited_discount), 2))


def suggest_discount(price: float, cost: float, min_margin_rate: float) -> float:
    return maximum_safe_discount(price, cost, min_margin_rate)


def suggest_discount_amount_yuan(
    price: float, cost: float, min_margin_rate: float
) -> dict[str, float | str | dict[str, float | str]]:
    amount = maximum_safe_discount(price, cost, min_margin_rate)
    promotion: dict[str, float | str] = (
        {"promotion_type": "none"}
        if amount == 0
        else {
            "promotion_type": "fixed_amount_coupon",
            "discount_amount_yuan": amount,
        }
    )
    return {
        "promotion_protocol_version": "1.0",
        "discount_amount_yuan": amount,
        "currency": "CNY",
        "promotion": promotion,
    }
