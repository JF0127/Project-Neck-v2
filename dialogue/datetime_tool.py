"""Local date/time context, adapted from Project-Neck/runtime/datetime_tool.py."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "third_party/probe"))
from probe import probe

SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


class CurrentDateTime:
    """Return the current wall clock in Asia/Shanghai."""

    def get_current_datetime(self) -> dict[str, str]:
        current = datetime.now(SHANGHAI_TIMEZONE)
        offset = current.strftime("%z")
        utc_offset = f"{offset[:3]}:{offset[3:]}"
        value = {
            "timezone": "Asia/Shanghai",
            "utc_offset": utc_offset,
            "datetime": current.isoformat(timespec="seconds"),
            "date": current.strftime("%Y-%m-%d"),
            "time": current.strftime("%H:%M:%S"),
            "weekday": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[current.weekday()],
        }
        probe("current_datetime_context", f"{value['datetime']} {value['weekday']}")
        return value
