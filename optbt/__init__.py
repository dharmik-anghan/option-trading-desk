"""The options backtesting engine.

A sibling of `backtest/`, not an extension of it: a structure of several legs,
contracts that stop existing on a date, and settlement at intrinsic value are
three things that engine's single-position loop cannot express.

`optbt.data` is the history it runs on - every NIFTY contract's one-minute bars,
from Fyers' expired F&O endpoints.
"""
