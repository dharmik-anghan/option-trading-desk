"""Option history: where it comes from, and where it is kept.

    source.py   what a source of option history must answer (Fyers' is
                broker/fyers/expired.py)
    store.py    DuckDB, with a ledger of which contracts are already held
    backfill.py deciding what to fetch, and fetching only what is missing
"""
