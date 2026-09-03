import re
import xml.sax
import xml.sax.handler
from typing import Any, BinaryIO, Dict, Iterator, List, Optional

from .dates import normalize_pub_date
from .model import FeedItem

_ITEM_FIELDS = {"title", "link", "description", "guid", "pubDate"}
_WHITESPACE_RE = re.compile(r"\s+")


def parse_stream(source: BinaryIO, chunk_size: int = 8192) -> Iterator[FeedItem]:
    """Yield FeedItem objects one at a time from an RSS 2.0 byte stream.

    `source` can be any binary file-like object: open(path, "rb"), a
    urllib response, a socket's makefile(), etc. Only the current item's
    partial state plus a small queue of finished-but-not-yet-yielded
    items are kept in memory, so parsing a multi-hundred-megabyte feed
    costs about the same as parsing a tiny one.
    """
    parser = xml.sax.make_parser()
    # Don't fetch external entities/DTDs referenced by the feed - a
    # malicious or misconfigured feed shouldn't cause us to make network
    # or filesystem requests while parsing.
    parser.setFeature(xml.sax.handler.feature_external_ges, False)
    parser.setFeature(xml.sax.handler.feature_external_pes, False)

    handler = _ItemHandler()
    parser.setContentHandler(handler)

    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            break
        parser.feed(chunk)
        while handler.ready_items:
            yield handler.ready_items.pop(0)

    parser.close()
    while handler.ready_items:
        yield handler.ready_items.pop(0)


class _ItemHandler(xml.sax.ContentHandler):
    def __init__(self) -> None:
        super().__init__()
        self._stack: List[str] = []
        self._fields: Dict[str, str] = {}
        self._current_field: Optional[str] = None
        self._buffer: List[str] = []
        self.ready_items: List[FeedItem] = []

    def startElement(self, name: str, attrs: Any) -> None:
        if name == "item":
            self._fields = {}
        elif (
            len(self._stack) == 1
            and self._stack[0] == "item"
            and name in _ITEM_FIELDS
        ):
            self._current_field = name
            self._buffer = []
        self._stack.append(name)

    def characters(self, content: str) -> None:
        if self._current_field is not None:
            self._buffer.append(content)

    def endElement(self, name: str) -> None:
        self._stack.pop()
        if name == self._current_field and len(self._stack) == 1:
            text = _WHITESPACE_RE.sub(" ", "".join(self._buffer)).strip()
            self._fields[name] = text
            self._current_field = None
        if name == "item" and self._stack and self._stack[-1] == "channel":
            self.ready_items.append(_build_item(self._fields))


def _build_item(fields: Dict[str, str]) -> FeedItem:
    raw_pub_date = fields.get("pubDate")
    return FeedItem(
        title=fields.get("title"),
        link=fields.get("link"),
        description=fields.get("description"),
        guid=fields.get("guid"),
        pub_date=normalize_pub_date(raw_pub_date),
        raw_pub_date=raw_pub_date,
    )
