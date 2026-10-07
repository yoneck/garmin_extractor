"""Live probe of Garmin API response shapes.

Run once from the project root:  python scripts/live_probe.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gar.extraction.login import connect  # noqa: E402
from gar.utils.config import load_from_env  # noqa: E402
from pathlib import Path as _P  # noqa: E402


def _resolve_env_path() -> _P:
    return _P(__file__).resolve().parents[2] / ".env"


def main() -> None:
    cfg = load_from_env(_resolve_env_path(), strict=True)
    client = connect(cfg)
    out = {}

    # 1. Sleep (single dailySleepDTO dict)
    for key in ("get_sleep_data",):
        try:
            val = getattr(client, key)("2026-09-09")
            out[key] = _trim(val) if val is not None else None
        except Exception as e:  # noqa: BLE001
            out[key] = f"ERROR: {e}"

    # 2. HRV
    try:
        out["get_hrv_data"] = _trim(client.get_hrv_data("2026-09-09"))
    except Exception as e:  # noqa: BLE001
        out["get_hrv_data"] = f"ERROR: {e}"

    # 3. Stress
    try:
        out["get_stress_data"] = _trim(client.get_stress_data("2026-09-09"))
    except Exception as e:  # noqa: BLE001
        out["get_stress_data"] = f"ERROR: {e}"

    # 4. Body Battery
    try:
        out["get_body_battery"] = _trim(client.get_body_battery("2026-09-09", "2026-09-09"))
    except Exception as e:  # noqa: BLE001
        out["get_body_battery"] = f"ERROR: {e}"

    # 5. Body Composition
    try:
        out["get_body_composition"] = _trim(
            client.get_body_composition("2026-09-02", "2026-09-09")
        )
    except Exception as e:  # noqa: BLE001
        out["get_body_composition"] = f"ERROR: {e}"

    print(json.dumps(out, indent=2, default=str))


def _trim(value, depth=0):
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _trim(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        head = [_trim(value[0], depth + 1)] if value else []
        return {"len": len(value), "first": head}
    if isinstance(value, (str, int, float, bool)):
        s = str(value)
        if len(s) > 40:
            return s[:40] + "..."
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} byte blob>"
    return str(value)


if __name__ == "__main__":
    main()
