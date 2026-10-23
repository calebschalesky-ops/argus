"""Trade filters every engine shares: the price band and maker pricing.

Plain words:
  - Price band. Cheap contracts on Kalshi lose most of their money (Burgi, Deng & Whelan
    2026: under 10c, buyers lost over 60%; above 50c, small gains). So by default Argus only
    flags a trade when the side you would buy costs 50c or more, and NEVER under 10c, even if
    someone configures a lower minimum. See research/maker_longshot.md.
  - Maker pricing. Instead of paying the ask ("taker"), you can rest a limit order one cent
    above the best bid ("maker"). You pay less and usually a smaller fee (zero on most series),
    but the order only fills when someone chooses to sell to you, which tends to happen when
    you are wrong (adverse selection). Paper bets that use maker pricing ASSUME the fill.

Any engine can call `check()` with its probability for a side and that side's ask/bid:

    from argus import filters
    q = filters.check(p_side=0.62, ask=0.55, bid=0.52)
    q.allowed            # True: 55c is inside the 50-99c band
    q.maker_price        # 0.53 (bid + 1c, never crossing the ask)
    q.flag(pricing="maker")   # "WORTH A LOOK" / "UNDER 50c" / "-"

Nothing here talks to Kalshi; it is pure arithmetic.
"""
from dataclasses import dataclass

from . import fees

TICK = 0.01
FLOOR = 0.10            # never flag a side priced under this, whatever the configured minimum
WORTH_A_LOOK_PTS = 10


@dataclass(frozen=True)
class Band:
    """Side prices Argus may flag: min_price..max_price, and never under FLOOR."""
    min_price: float = 0.50
    max_price: float = 0.99

    @property
    def low(self):
        return max(self.min_price, FLOOR)

    def allows(self, price):
        return price is not None and self.low - 1e-9 <= price <= self.max_price + 1e-9

    def reason(self, price):
        """Short label for a rejected price ("UNDER 50c"), or "" when allowed."""
        if price is None:
            return "NO PRICE"
        if price < self.low - 1e-9:
            return f"UNDER {round(self.low * 100)}c"
        if price > self.max_price + 1e-9:
            return f"OVER {round(self.max_price * 100)}c"
        return ""

    def describe(self):
        return f"side price {round(self.low * 100)}-{round(self.max_price * 100)}c (never under {round(FLOOR * 100)}c)"


DEFAULT_BAND = Band()
NO_BAND = Band(min_price=0.0, max_price=0.99)   # still never under FLOOR


def maker_price(bid, ask, tick=TICK):
    """Price of a resting buy order one tick above the best bid, never crossing the ask.

    If the spread is only one tick, bid + 1c would BE the ask (an instant taker fill), so the
    order joins the best bid instead. Returns None when no sensible resting price exists.
    """
    q = round((bid or 0) + tick, 4)
    if ask is not None and 0 < ask < 1 and q >= ask - 1e-9:
        q = bid
    return q if q is not None and 0 < q < 1 else None


def side_book(side, yes_bid, yes_ask):
    """(ask, bid) for buying `side`. Buying No at x = someone selling Yes at 1 - x."""
    if side == "YES":
        return yes_ask, yes_bid
    no_ask = round(1 - yes_bid, 4) if yes_bid else None
    no_bid = round(1 - yes_ask, 4) if yes_ask and yes_ask < 1 else None
    return no_ask, no_bid


@dataclass
class Quote:
    p_side: float
    ask: float | None
    bid: float | None
    maker_price: float | None
    taker_fee: float | None
    maker_fee: float | None
    taker_ev: float | None
    maker_ev: float | None
    band: Band

    def price(self, pricing="taker"):
        return self.maker_price if pricing == "maker" else self.ask

    def fee(self, pricing="taker"):
        return self.maker_fee if pricing == "maker" else self.taker_fee

    def ev(self, pricing="taker"):
        return self.maker_ev if pricing == "maker" else self.taker_ev

    def gap(self, pricing="taker"):
        p = self.price(pricing)
        return None if p is None else (self.p_side - p) * 100

    @property
    def allowed(self):
        return self.band.allows(self.ask)

    def flag(self, pricing="taker", min_gap=WORTH_A_LOOK_PTS):
        """WORTH A LOOK only if the gap is big enough AND the price is inside the band."""
        gap, price = self.gap(pricing), self.price(pricing)
        if gap is None or gap < min_gap - 1e-9:      # 0.65 - 0.55 is 9.999... in floating point
            return "-"
        return self.band.reason(price) or "WORTH A LOOK"


def check(p_side, ask, bid=None, band=DEFAULT_BAND, maker_fees=False, fee_multiplier=1, contracts=1):
    """Price one side both ways (take the ask / rest at bid + 1c) and test it against the band.

    p_side: your probability that this side wins. ask/bid: that side's best ask and bid in dollars.
    maker_fees: does this series charge makers (fees.series_maker_fees(series) tells you)?
    """
    band = band or NO_BAND
    ask = ask if ask is not None and 0 < ask < 1 else None
    mp = maker_price(bid, ask)
    tf = fees.taker_fee(ask, contracts, fee_multiplier) if ask else None
    mf = fees.maker_fee(mp, contracts, fee_multiplier, maker_fees) if mp else None
    return Quote(p_side, ask, bid, mp, tf, mf,
                 fees.expected_value(p_side, ask, contracts, tf) if ask else None,
                 fees.expected_value(p_side, mp, contracts, mf) if mp else None,
                 band)
