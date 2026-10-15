"""Live check: fetch every feed in starter_feeds.yaml through the real fetcher.

Run with: python tests/check_starter_feeds.py
Exits non-zero if any feed fails. Uses the network, so it is not a pytest test.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fetcher import FeedFetcher  # noqa: E402
from app.main import load_starter_feeds  # noqa: E402


async def main() -> int:
    data = load_starter_feeds()
    fetcher = FeedFetcher()
    failures = 0
    try:
        for category in data["categories"]:
            for feed in category["feeds"]:
                try:
                    result = await fetcher.get_feed(feed["url"])
                    count = len(result["items"])
                    status = "OK  " if count else "FAIL"
                    if not count:
                        failures += 1
                    print(f"{status} {count:3d} items  {feed['name']}  {feed['url']}")
                except Exception as exc:  # report and continue
                    failures += 1
                    print(f"FAIL   error    {feed['name']}  {feed['url']}  ({type(exc).__name__}: {exc})")
    finally:
        await fetcher.aclose()
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
