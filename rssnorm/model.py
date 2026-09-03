from dataclasses import dataclass
from typing import Optional


@dataclass
class FeedItem:
    """A single normalized <item> from an RSS feed.

    Fields are None when the source item didn't have them, rather than
    empty strings, so callers can tell "missing" apart from "empty".
    """

    title: Optional[str]
    link: Optional[str]
    description: Optional[str]
    guid: Optional[str]
    pub_date: Optional[str]      # normalized UTC ISO 8601, e.g. "2026-09-04T08:00:00Z"
    raw_pub_date: Optional[str]  # whatever string the feed actually had, unmodified
