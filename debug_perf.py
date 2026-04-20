"""
Lightweight call-rate tracker. Import and use track() to count how often
each label is hit per second. A QTimer prints the report every 3 seconds.

Usage:
    from debug_perf import track
    track("poll_status")   # call inside the function you want to measure

Set ENABLED = False to disable completely with zero overhead.
"""
from collections import defaultdict
import time

ENABLED = True

_counts: dict[str, int] = defaultdict(int)
_last_report = time.time()
_INTERVAL = 3.0  # seconds between reports


def track(label: str):
    if not ENABLED:
        return
    global _last_report
    _counts[label] += 1
    now = time.time()
    if now - _last_report >= _INTERVAL:
        _last_report = now
        _print_report()


def _print_report():
    if not _counts:
        return
    print("\n── perf report (calls/sec over last 3s) ──")
    for label, n in sorted(_counts.items()):
        print(f"  {label:<35} {n / _INTERVAL:6.1f} /s   ({n} calls)")
    print("──────────────────────────────────────────\n")
    _counts.clear()
