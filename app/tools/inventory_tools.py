from __future__ import annotations


def check_inventory(inventory: int, planned_units: int) -> dict[str, object]:
    return {
        "inventory": inventory,
        "planned_units": planned_units,
        "valid": inventory >= planned_units,
        "remaining": inventory - planned_units,
    }
