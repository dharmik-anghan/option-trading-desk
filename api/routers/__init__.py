"""HTTP routers, one module per area of the desk.

`api/app.py` was a single 900-line module holding every endpoint plus the
helpers they shared, which was fine while there was one venue and one asset
class. A second desk doubles the endpoint count, so the file is split by what
each group is about instead. `app.py` keeps only composition: the FastAPI
instance, middleware, the error handler, and the routers it includes.
"""
