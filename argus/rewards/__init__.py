"""Kalshi Liquidity Incentive Program: what a small resting order would earn.

Read-only, like the rest of Argus. Reads the public reward list (/incentive_programs) and
public order books, re-runs Kalshi's published scoring formula with a hypothetical order added,
and estimates reward per day. It never places an order. Research notes: research/rewards.md.
"""
