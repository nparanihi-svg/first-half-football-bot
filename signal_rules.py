"""Explainable, experimental in-play watchlist filters (NOT validated bet advice)."""

from alert_policy import selection_shots

def watchlist_reason(snapshot, market, *, require_blocked=False, min_shots=5):
    minute = int(snapshot.elapsed)
    if snapshot.status != '1H' or not 8 <= minute <= 38:
        return None
    shots = selection_shots(snapshot)
    on_target = snapshot.shots_on_goal
    blocked = snapshot.blocked_shots
    if market == 'FH_OVER_0_5_GOALS':
        if snapshot.half_goals != 0 or shots is None or on_target is None:
            return None
        if shots >= min_shots and on_target >= 2:
            return f'at least {shots} shots and {on_target} shots on target by minute {minute}'
    elif market == 'FH_OVER_2_5_CORNERS':
        if snapshot.half_corners is None or snapshot.half_corners >= 3:
            return None
        if shots is None:
            return None
        if snapshot.half_corners >= 1 and shots >= min_shots and (not require_blocked or (blocked is not None and blocked >= 1)):
            return (f'{snapshot.half_corners} corners, {shots} shots and '
                    f'{blocked if blocked is not None else "unknown"} blocked shots by minute {minute}')
    return None
