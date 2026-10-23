"""Kalshi's liquidity-reward scoring, transcribed from the Oct 14, 2026 CFTC filing (Appendix B, section 5).

Per side of the book (a yes ask counts as a no bid):
  walk down from the best bid, adding whole price levels; the Reference Price is the first level where
  cumulative size >= Target/5; stop at the level where cumulative size >= Target. If the side never
  reaches Target, nothing on it qualifies and the snapshot is excluded.
  score(order) = size x DF ** N, N = ticks below the Reference Price (0 at or above it).
  An account's share of a side = its score / all scores on that side.
Snapshot score = yes share + no share (at most 2.0 across everyone). Reward for the Time Period =
(your snapshot scores / everyone's) x reward x (non-excluded / all snapshots); paid only if >= $1.00.
"""


def side_scores(levels, target, discount=0.5, tick=0.01):
    """levels: [(price, size), ...] bids on one side, any order.

    Returns (reference_price, [(price, size, score), ...]) for the qualifying levels,
    or (None, []) when the side does not reach the Target Size.
    """
    levels = sorted(((float(p), float(s)) for p, s in levels if float(s) > 0), reverse=True)
    cum, ref, picked = 0.0, None, []
    for price, size in levels:
        cum += size
        picked.append((price, size))
        if ref is None and cum >= target / 5:
            ref = price
        if cum >= target:
            out = []
            for p, s in picked:
                n = max(0, round((ref - p) / tick))
                out.append((p, s, s * discount ** n))
            return ref, out
    return None, []


def my_share(levels, my_price, my_size, target, discount=0.5, tick=0.01):
    """Share of one side's score that an extra order of `my_size` at `my_price` would get.

    The order is added to the book first (it counts toward the Target and can move the Reference
    Price). Within a price level it scores pro rata with everyone else at that price.
    Returns (share, reference_price, side_qualifies).
    """
    book = {}
    for p, s in levels:
        p = round(float(p), 4)
        book[p] = book.get(p, 0.0) + float(s)
    mp = round(float(my_price), 4)
    others_at = book.get(mp, 0.0)
    book[mp] = others_at + my_size
    ref, scored = side_scores(book.items(), target, discount, tick)
    if ref is None:
        return 0.0, None, False
    total = sum(sc for _, _, sc in scored)
    mine = sum(sc * my_size / s for p, s, sc in scored if p == mp)
    return (mine / total if total else 0.0), ref, True


def reward_per_day(pool_per_day, yes_share, no_share, uptime=1.0):
    """Dollars a day to one account: the pool is split over 2 points per snapshot (one per side)."""
    return pool_per_day * (yes_share + no_share) / 2 * uptime
