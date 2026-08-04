from collections import defaultdict, deque
from time import monotonic

_WINDOW   = 300  # 5 minutes
_MAX_MSGS = 20

_timestamps: dict[int, deque] = defaultdict(deque)
_warned:     dict[int, float] = {}


def is_allowed(uid: int) -> bool:
    """Consume one slot. Returns False (without consuming) when over limit."""
    now = monotonic()
    dq  = _timestamps[uid]
    while dq and now - dq[0] > _WINDOW:
        dq.popleft()
    if len(dq) >= _MAX_MSGS:
        return False
    dq.append(now)
    return True


def should_warn(uid: int) -> bool:
    """True only for the first refusal in each burst — suppresses repeat warnings."""
    now  = monotonic()
    last = _warned.get(uid, 0.0)
    if now - last > _WINDOW:
        _warned[uid] = now
        return True
    return False
