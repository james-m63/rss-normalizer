import re
import xml.sax
import xml.sax.handler
from typing import Any, BinaryIO, Dict, Iterator, List, Optional

from .dates import normalize_pub_date
from .model import FeedItem

_CONTAINER_TAGS = {"item": "channel", "entry": "feed"}
_CONTAINER_FIELDS = {
    "item": {"title", "link", "description", "guid", "pubDate"},
    "entry": {"title", "link", "summary", "content", "id", "published", "updated"},
}
_WHITESPACE_RE = re.compile(r"\s+")


def parse_stream(source: BinaryIO, chunk_size: int = 8192) -> Iterator[FeedItem]:
    """Yield FeedItem objects one at a time from an RSS 2.0 or Atom byte stream.

    `source` can be any binary file-like object: open(path, "rb"), a
    urllib response, a socket's makefile(), etc. Only the current item's
    partial state plus a small queue of finished-but-not-yet-yielded
    items are kept in memory, so parsing a multi-hundred-megabyte feed
    costs about the same as parsing a tiny one.

    Both RSS 2.0 `<item>` and Atom `<entry>` elements are recognized and
    mapped onto the same FeedItem shape (Atom's `id`/`summary`/`published`
    become `guid`/`description`/`pub_date`).
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
        self._container: Optional[str] = None
        self._fields: Dict[str, str] = {}
        self._current_field: Optional[str] = None
        self._buffer: List[str] = []
        self.ready_items: List[FeedItem] = []

    def startElement(self, name: str, attrs: Any) -> None:
        if name in _CONTAINER_TAGS:
            self._container = name
            self._fields = {}
        elif (
            self._container is not None
            and len(self._stack) == 1
            and self._stack[0] == self._container
            and name in _CONTAINER_FIELDS[self._container]
        ):
            if name == "link" and self._container == "entry":
                # Atom <link> carries its URL in an href attribute and is
                # usually self-closing, not text content. A feed can list
                # several (self, alternate, ...); take the first alternate.
                if "link" not in self._fields and attrs.get("rel", "alternate") == "alternate":
                    href = attrs.get("href")
                    if href:
                        self._fields["link"] = href
            else:
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
        if (
            name in _CONTAINER_TAGS
            and self._stack
            and self._stack[-1] == _CONTAINER_TAGS[name]
        ):
            builder = _build_rss_item if name == "item" else _build_atom_entry
            self.ready_items.append(builder(self._fields))
            self._container = None


def _build_rss_item(fields: Dict[str, str]) -> FeedItem:
    raw_pub_date = fields.get("pubDate")
    return FeedItem(
        title=fields.get("title"),
        link=fields.get("link"),
        description=fields.get("description"),
        guid=fields.get("guid"),
        pub_date=normalize_pub_date(raw_pub_date),
        raw_pub_date=raw_pub_date,
    )


def _build_atom_entry(fields: Dict[str, str]) -> FeedItem:
    # <published> is when the entry was created; <updated> is the last
    # modification and is the only one Atom requires, so fall back to it.
    raw_pub_date = fields.get("published") or fields.get("updated")
    return FeedItem(
        title=fields.get("title"),
        link=fields.get("link"),
        description=fields.get("summary") or fields.get("content"),
        guid=fields.get("id"),
        pub_date=normalize_pub_date(raw_pub_date),
        raw_pub_date=raw_pub_date,
    )
