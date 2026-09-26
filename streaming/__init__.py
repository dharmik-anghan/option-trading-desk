"""Live prices, held once and shared.

A stream has one connection and many readers: the alert watcher wants the latest
price to judge a level against, a browser wants every update, and a panel wants
whatever is current when it renders. This package is the seam between them, so
the venue adapters stay ignorant of who is listening.
"""

from streaming.hub import TickHub

__all__ = ["TickHub"]
