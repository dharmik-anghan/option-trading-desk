from __future__ import annotations

from pathlib import Path

import pytest

from feeds.news_parse import parse_rss

FIXTURES = Path(__file__).parent / "fixtures"


def test_cdata_and_markup_are_stripped_from_titles() -> None:
    items = parse_rss((FIXTURES / "rss_cdata.xml").read_bytes(), "Markets")

    titles = [h.title for h in items]
    assert "Nifty ends higher as banks lead gains" in titles  # <b> removed
    # a publisher leaking its own CDATA terminator into the text
    assert "Biscuit maker files for $188 million IPO" in titles


def test_a_feed_that_declares_utf8_but_sends_no_charset_header_still_parses() -> None:
    """The bug this pins.

    RBI's feed sends no charset header and opens with a UTF-8 byte-order mark.
    Decoding it to text with a guessed encoding first turns that mark into
    "ï»¿" ahead of the XML declaration, which is not well-formed XML, and the
    feed came back empty. Parsing the raw bytes honours the declaration.
    """
    raw = (FIXTURES / "rss_bom_no_charset.xml").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    items = parse_rss(raw, "RBI")

    assert len(items) == 2
    # and the en dash survives, which is the other half of getting encoding right
    assert items[0].title == "RBI Bulletin – September 2026"


def test_decoding_to_text_first_is_what_broke_it() -> None:
    # the same bytes read as Latin-1, which is what an HTTP client guessed
    mojibake = (FIXTURES / "rss_bom_no_charset.xml").read_bytes().decode("latin-1")
    assert parse_rss(mojibake, "RBI") == []


def test_dates_are_parsed_and_the_newest_comes_first() -> None:
    items = parse_rss((FIXTURES / "rss_bom_no_charset.xml").read_bytes(), "RBI")
    assert items[0].published is not None
    assert items[1].published is not None
    assert items[0].published > items[1].published


def test_undated_items_are_kept_and_sort_last() -> None:
    items = parse_rss((FIXTURES / "rss_cdata.xml").read_bytes(), "Markets")

    dated = [h for h in items if h.published is not None]
    undated = [h for h in items if h.published is None]
    assert len(dated) == 2
    assert len(undated) == 2
    # two undated items would compare None with None on a naive sort, which
    # raises; they must simply end up at the back
    assert [h.published is None for h in items] == [False, False, True, True]


def test_an_item_with_no_title_is_dropped() -> None:
    items = parse_rss((FIXTURES / "rss_cdata.xml").read_bytes(), "Markets")
    assert all(h.title for h in items)
    assert not any(h.link.endswith("/empty") for h in items)


def test_the_source_is_carried_through() -> None:
    items = parse_rss((FIXTURES / "rss_cdata.xml").read_bytes(), "Business Standard")
    assert {h.source for h in items} == {"Business Standard"}


@pytest.mark.parametrize(
    "payload", [b"", b"not xml", b"<rss><channel><item><title>x</title></item>", "", "junk"]
)
def test_malformed_input_costs_its_own_headlines_and_nothing_else(payload: bytes | str) -> None:
    assert parse_rss(payload, "t") == []
