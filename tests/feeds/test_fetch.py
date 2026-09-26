from __future__ import annotations

from datetime import date
from pathlib import Path

from feeds.fetch import CALENDAR_TTL, NEWS_TTL, Feeds, upcoming
from feeds.models import Event
from feeds.sources import CALENDAR_URL, NEWS_SOURCES, Topic, sources_for

FIXTURES = Path(__file__).parent / "fixtures"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Net:
    """A stand-in for the network, recording what was asked for."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: Exception | None = None
        self.calendar = (FIXTURES / "calendar.html").read_bytes()
        self.rss = (FIXTURES / "rss_cdata.xml").read_bytes()
        self.fail_hosts: set[str] = set()

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if self.fail is not None:
            raise self.fail
        if any(h in url for h in self.fail_hosts):
            raise OSError("refused")
        return self.calendar if url == CALENDAR_URL else self.rss


def _feeds() -> tuple[Feeds, Net, Clock]:
    net, clock = Net(), Clock()
    return Feeds(now=clock, get=net), net, clock


class TestCaching:
    def test_the_calendar_is_fetched_once_within_its_ttl(self) -> None:
        feeds, net, clock = _feeds()

        feeds.events()
        clock.advance(CALENDAR_TTL / 2)
        feeds.events()

        assert net.calls.count(CALENDAR_URL) == 1

    def test_and_again_once_it_has_expired(self) -> None:
        feeds, net, clock = _feeds()

        feeds.events()
        clock.advance(CALENDAR_TTL + 1)
        feeds.events()

        assert net.calls.count(CALENDAR_URL) == 2

    def test_news_has_its_own_shorter_ttl(self) -> None:
        feeds, net, clock = _feeds()

        feeds.headlines()
        first = len(net.calls)
        clock.advance(NEWS_TTL / 2)
        feeds.headlines()
        assert len(net.calls) == first

        clock.advance(NEWS_TTL + 1)
        feeds.headlines()
        assert len(net.calls) > first

    def test_age_is_reported_so_the_desk_can_say_as_of(self) -> None:
        feeds, _net, clock = _feeds()

        cached = feeds.events()
        assert feeds.age_seconds(cached) == 0.0
        clock.advance(120)
        assert feeds.age_seconds(feeds.events()) == 120.0


class TestDegrading:
    def test_a_failed_refresh_keeps_the_last_good_calendar(self) -> None:
        feeds, net, clock = _feeds()
        good = feeds.events()
        assert good.events

        net.fail = OSError("no route to host")
        clock.advance(CALENDAR_TTL + 1)
        after = feeds.events()

        # stale, and saying so - not empty
        assert len(after.events) == len(good.events)
        assert after.error is not None

    def test_a_page_that_parses_to_nothing_is_reported_as_unreadable(self) -> None:
        """What a markup change looks like: reachable, but no events in it."""
        feeds, net, _clock = _feeds()
        net.calendar = b"<html><body>redesigned</body></html>"

        cached = feeds.events()

        assert cached.events == []
        assert cached.error is not None
        assert "no events could be read" in cached.error

    def test_one_news_source_down_is_not_an_outage(self) -> None:
        feeds, net, _clock = _feeds()
        net.fail_hosts = {"rbi.org.in"}

        cached = feeds.headlines()

        assert cached.headlines  # the other three still answered
        assert cached.error is not None
        assert "RBI" in cached.error

    def test_every_source_down_says_so(self) -> None:
        feeds, net, _clock = _feeds()
        net.fail = OSError("offline")

        cached = feeds.headlines()

        assert cached.headlines == []
        assert cached.error == "No news source could be reached."

    def test_headlines_from_every_source_are_pooled_and_ordered(self) -> None:
        feeds, _net, _clock = _feeds()

        cached = feeds.headlines()

        # Derived, not hardcoded: adding a source should not fail this test, and
        # the thing worth asserting is that every one of them made it in.
        assert len({h.source for h in cached.headlines}) == len(NEWS_SOURCES)
        timed = [h.published.timestamp() for h in cached.headlines if h.published]
        assert timed == sorted(timed, reverse=True)


class TestUpcoming:
    def _events(self) -> list[Event]:
        return [
            Event(day=date(2026, 10, 1), name="Yesterday", importance="H", coverage="india"),
            Event(day=date(2026, 10, 7), name="RBI Policy Rate", importance="H", coverage="india"),
            Event(day=date(2026, 10, 9), name="Minor", importance="L", coverage="india"),
            Event(day=date(2026, 10, 12), name="CPI", importance="M", coverage="india"),
            Event(day=date(2026, 12, 1), name="Far off", importance="H", coverage="global"),
        ]

    def test_only_events_in_the_window_ahead(self) -> None:
        got = upcoming(self._events(), date(2026, 10, 5), days=30, importance="HML")
        assert [e.name for e in got] == ["RBI Policy Rate", "Minor", "CPI"]

    def test_importance_filters(self) -> None:
        got = upcoming(self._events(), date(2026, 10, 5), days=30, importance="H")
        assert [e.name for e in got] == ["RBI Policy Rate"]

    def test_today_counts_as_upcoming(self) -> None:
        got = upcoming(self._events(), date(2026, 10, 7), days=0, importance="H")
        assert [e.name for e in got] == ["RBI Policy Rate"]

    def test_nothing_in_the_past(self) -> None:
        got = upcoming(self._events(), date(2026, 11, 1), days=5, importance="HML")
        assert got == []


class TestTopics:
    """Filtering on the source's beat rather than on the words in a headline."""

    def test_every_source_declares_at_least_one_topic(self) -> None:
        # A source with no topic is invisible to every filter, which is a silent
        # way to drop a feed.
        for source in NEWS_SOURCES:
            assert source.topics, f"{source.name} declares no topic"

    def test_topics_are_real_ones(self) -> None:
        for source in NEWS_SOURCES:
            for topic in source.topics:
                assert topic in set(Topic)

    def test_both_desks_have_sources(self) -> None:
        # The point of the exercise: the crypto desk had no news of its own.
        covered = {t for s in NEWS_SOURCES for t in s.topics}
        assert Topic.INDIA in covered
        assert Topic.CRYPTO in covered
        assert Topic.COMMODITIES in covered

    def test_selecting_a_topic_narrows_the_sources(self) -> None:
        crypto = sources_for(frozenset({Topic.CRYPTO}))
        assert crypto
        assert len(crypto) < len(NEWS_SOURCES)
        assert all(Topic.CRYPTO in s.topics for s in crypto)

    def test_selecting_nothing_means_everything(self) -> None:
        assert sources_for(frozenset()) == NEWS_SOURCES

    def test_a_desk_can_ask_for_two_topics(self) -> None:
        both = sources_for(frozenset({Topic.CRYPTO, Topic.COMMODITIES}))
        names = {s.name for s in both}
        assert "CoinDesk" in names
        assert "OilPrice" in names
        assert "RBI" not in names

    def test_headlines_carry_their_source_topics(self) -> None:
        feeds, _net, _clock = _feeds()
        for headline in feeds.headlines().headlines:
            source = next(s for s in NEWS_SOURCES if s.name == headline.source)
            assert headline.topics == {str(t) for t in source.topics}
