from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from app.analytics.models import TimeRange


def parse_time_range(text: str, *, today: date | None = None) -> TimeRange:
    """Parse supported business periods deterministically; no LLM date arithmetic."""

    end = today or date.today()
    match = re.search(r"(?:最近|过去|近)\s*(\d+)\s*天", text)
    if match:
        days = max(1, min(365, int(match.group(1))))
        return TimeRange(
            start_date=end - timedelta(days=days - 1),
            end_date=end,
            label=f"最近 {days} 天",
            comparison_mode="previous_period" if re.search(r"对比|环比|变化|趋势", text) else "none",
        )
    if "上个月" in text:
        first_this_month = end.replace(day=1)
        last_previous = first_this_month - timedelta(days=1)
        first_previous = last_previous.replace(day=1)
        return TimeRange(
            start_date=first_previous,
            end_date=last_previous,
            label="上个月",
            comparison_mode="previous_period" if re.search(r"对比|环比|变化|趋势", text) else "none",
        )
    if re.search(r"本月|这个月", text):
        return TimeRange(
            start_date=end.replace(day=1),
            end_date=end,
            label="本月",
        )
    if re.search(r"活动前后|促销前后", text):
        return TimeRange(
            start_date=end - timedelta(days=59),
            end_date=end,
            label="最近活动前后",
            comparison_mode="campaign_window",
        )
    if re.search(r"最近|近期|销售|表现|销量", text):
        return TimeRange(
            start_date=end - timedelta(days=29),
            end_date=end,
            label="最近 30 天",
        )
    days_in_month = calendar.monthrange(end.year, end.month)[1]
    return TimeRange(
        start_date=end - timedelta(days=min(29, days_in_month - 1)),
        end_date=end,
        label="最近 30 天",
    )
