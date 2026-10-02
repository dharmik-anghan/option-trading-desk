"""What the desk does with nobody watching: loops the app starts at boot.

    alert_watcher.py    judge the structures and positions, notify what fired
    vol_recorder.py     write down what options cost, each session
    preopen_recorder.py write down NSE's pre-open auction, each session
    daily_bars.py       keep the daily bars the rotation graph and ranks read

Each takes its sources as callables, so a test runs one pass without a broker,
a network or a clock. `api/app.py` wires them and owns their lifetime.
"""
