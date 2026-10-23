"""The trial registry: one row for every rule or variant the factory ever scored (lab_trials.csv).

Plain words: if you try 3,000 rules, about 150 look good at p < 0.05 by luck alone. The only
defence is to count every try, including the ones that failed and the ones that were never
written up, and to correct for all of them at once. Every miner rule with enough events, every
`bench` run, every order-size re-score and every scan writes a row here, with the date it was
first scored. The miner's false-discovery control runs over the whole registry, not just over
today's batch, so each new batch has to clear a bar raised by everything tried before it.
"""
import csv
from datetime import datetime, timezone

import numpy as np

from . import ROOT, stats

TRIALS = ROOT / "lab_trials.csv"
COLS = ["trial_id", "kind", "name", "batch", "scored_at_utc", "data_window", "n", "events", "roi", "p", "note"]


def load(path=None):
    path = path or TRIALS
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append(rows, path=None):
    path = path or TRIALS
    new = not path.exists()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        for r in rows:
            r = dict(r)
            r.setdefault("scored_at_utc", now)
            r["trial_id"] = r.get("trial_id") or f"{r['batch']}|{r['name']}"
            for k in ("roi", "p"):
                if isinstance(r.get(k), float):
                    r[k] = f"{r[k]:.4g}"
            w.writerow({k: r.get(k, "") for k in COLS})


def prior_p(exclude_batch=None, path=None):
    """p-values of every earlier trial (rows of other batches), for the registry-wide correction."""
    out = []
    for r in load(path):
        if r["batch"] == exclude_batch:
            continue
        try:
            out.append(float(r["p"]))
        except (TypeError, ValueError):
            out.append(1.0)          # scored but no p recorded: still counts as a try
    return np.array(out)


def bh_with_registry(p_now, q, exclude_batch=None, path=None):
    """BH discoveries among p_now, corrected over p_now AND every earlier trial in the registry."""
    p_now = np.asarray(p_now, dtype=float)
    allp = np.concatenate([p_now, prior_p(exclude_batch, path)])
    return stats.benjamini_hochberg(allp, q)[:len(p_now)], len(allp)
