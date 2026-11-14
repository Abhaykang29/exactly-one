"""maybe_crash(point): simulated hard crash for the demo (os._exit(137))."""
from __future__ import annotations

import os
import random
import sys
import time

_rng = random.Random(time.time_ns() ^ os.getpid())


def maybe_crash(point: str) -> None:
    p = float(os.environ.get("CRASH_PROB", "0") or 0)
    if p > 0 and _rng.random() < p:
        print(f"[crash] {point}", flush=True)
        sys.stdout.flush()
        os._exit(137)
