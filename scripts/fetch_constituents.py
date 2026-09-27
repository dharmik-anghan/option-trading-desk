"""Who is in each index, from NSE's own published lists.

    python scripts/fetch_constituents.py           # fetch and save
    python scripts/fetch_constituents.py --show    # what is held already

Saved to `data/constituents.json` with the date fetched. Membership changes, and
a rotation graph drawn over two years using today's list is measuring a universe
that did not exist then - so the date is recorded, and a reader can see how old
it is.

NSE answers an unknown file name with a web page rather than a 404, so anything
that does not parse as a constituent list is reported rather than saved.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from universe.nse import INDICES, fetch, load, save  # noqa: E402

#: Between requests. NSE is somebody else's archive and this runs rarely.
BETWEEN = 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="print what is held, then stop")
    args = parser.parse_args()

    if args.show:
        held = load()
        if not held:
            print("Nothing held. Run without --show to fetch.")
            return 0
        for index_id, members in sorted(held.items()):
            print(f"{index_id:18} {len(members.members):>3} members  as at {members.at:%Y-%m-%d}")
        return 0

    session = requests.Session()
    out = load()
    failed: list[str] = []
    for spec in INDICES:
        if spec.members_file is None:
            print(f"{spec.id:18} no list published; usable as a benchmark only")
            continue
        got = fetch(spec, session)
        if got is None:
            failed.append(spec.id)
            print(f"{spec.id:18} FAILED  ({spec.members_file})")
        else:
            out[spec.id] = got
            print(f"{spec.id:18} {len(got.members):>3} members")
        time.sleep(BETWEEN)

    save(out)
    print(f"\nSaved {len(out)} lists.")
    if failed:
        print(f"Could not fetch: {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
