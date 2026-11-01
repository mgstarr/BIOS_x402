"""Mandate enforcement: tracks spend against a Mandate's per-payment and budget limits."""

DEFAULT_MANDATE = {"max_per_payment_usd": 0.10, "budget_usd": 0.50}


def parse_mandate(context: dict) -> dict:
    """Mandate from the request context, filling gaps with the defaults. Bad numbers -> defaults."""
    raw = context.get("mandate") if isinstance(context.get("mandate"), dict) else {}
    out = {}
    for key, default in DEFAULT_MANDATE.items():
        try:
            out[key] = float(raw.get(key, default))
        except (TypeError, ValueError):
            out[key] = default
    out["task"] = str(context.get("task") or raw.get("task") or "default")
    return out


def money(usd: float) -> str:
    """0.02 -> '2¢', 0.95 -> '95¢', 0.005 -> '0.5¢', 1 -> '$1.00', 90 -> '$90.00'."""
    if usd >= 1:
        return f"${usd:,.2f}"
    return f"{round(usd * 100, 2):g}¢"


def check_mandate(amount_usd: float, mandate: dict, spent_usd: float) -> tuple[bool, str]:
    """Pass if this payment fits the per-payment limit and the task's total budget."""
    limit, budget = mandate["max_per_payment_usd"], mandate["budget_usd"]
    if amount_usd > limit + 1e-9:
        return False, f"This bill is {money(amount_usd)}, but the agent may only pay {money(limit)} at a time"
    total = spent_usd + amount_usd
    if total > budget + 1e-9:
        return False, (f"Paying {money(amount_usd)} would bring this task to {money(total)}, "
                       f"over its {money(budget)} budget")
    return True, f"{money(amount_usd)} fits (limit {money(limit)}, spent {money(spent_usd)} of {money(budget)})"
