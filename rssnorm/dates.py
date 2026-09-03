import datetime
import email.utils
from typing import Optional


def normalize_pub_date(raw: Optional[str]) -> Optional[str]:
    """Parse an RSS pubDate-ish string into a UTC ISO 8601 string.

    RSS 2.0 specifies RFC 822 dates, but real feeds also show up with plain
    ISO 8601 (common when a feed is generated from an Atom source), or with
    junk that doesn't parse as either. We try RFC 822 first since that's
    the spec, fall back to ISO 8601, and give up to None rather than guess.
    """
    if raw is None:
        return None

    raw = raw.strip()
    if not raw:
        return None

    parsed = _try_rfc822(raw)
    if parsed is None:
        parsed = _try_iso8601(raw)
    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    parsed = parsed.astimezone(datetime.timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _try_rfc822(raw: str) -> Optional[datetime.datetime]:
    try:
        return email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def _try_iso8601(raw: str) -> Optional[datetime.datetime]:
    try:
        # datetime.fromisoformat doesn't accept a bare "Z" suffix.
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
