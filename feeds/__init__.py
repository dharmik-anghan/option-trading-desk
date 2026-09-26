"""External data feeds: the economic calendar and news headlines.

Separate from `broker/` because none of it comes from a broker, and separate
from `analytics/` because it is fetched rather than derived. Parsing is kept
apart from fetching throughout, so every parser can be tested against a saved
page and the test suite never touches the network.
"""
