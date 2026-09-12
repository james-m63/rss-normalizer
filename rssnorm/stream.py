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

# Real feeds routinely put raw HTML into <description> without CDATA-wrapping
# or escaping it, e.g. "Fish & Chips" or "&nbsp;" copy-pasted from a CMS.
# Plain XML rejects both outright (undefined entity / bare ampersand), which
# would otherwise kill the whole parse over one bad item. We rewrite these
# before they reach the SAX parser rather than trying to catch and skip the
# resulting SAXParseException, since that exception aborts the parser and
# there is no clean way to resume mid-document.
_XML_ENTITY_NAMES = {b"amp", b"lt", b"gt", b"apos", b"quot"}
_HTML_ENTITY_CODEPOINTS = {
    b"nbsp": 0x00A0,
    b"mdash": 0x2014,
    b"ndash": 0x2013,
    b"hellip": 0x2026,
    b"ldquo": 0x201C,
    b"rdquo": 0x201D,
    b"lsquo": 0x2018,
    b"rsquo": 0x2019,
    b"copy": 0x00A9,
    b"reg": 0x00AE,
    b"trade": 0x2122,
    b"deg": 0x00B0,
    b"middot": 0x00B7,
}
_AMP_RE = re.compile(rb"&(#x[0-9a-fA-F]+;?|#[0-9]+;?|[a-zA-Z][a-zA-Z0-9]*;?)?")


def _replace_amp(match: "re.Match[bytes]") -> bytes:
    body = match.group(1)
    if not body:
        return b"&amp;"

    has_semicolon = body.endswith(b";")
    core = body[:-1] if has_semicolon else body

    if core.startswith(b"#") or core in _XML_ENTITY_NAMES:
        return b"&" + core + b";"

    codepoint = _HTML_ENTITY_CODEPOINTS.get(core)
    if codepoint is not None:
        return "&#{};".format(codepoint).encode("ascii")

    # Unrecognized name (real or bogus) - escape the ampersand and leave
    # the rest as literal text rather than guessing at its meaning.
    return b"&amp;" + body


_CDATA_START = b"<![CDATA["
_CDATA_END = b"]]>"


def _partial_prefix_len(data: bytes, pos: int, end: int, marker: bytes) -> int:
    """Length of the longest suffix of data[pos:end] that is also a proper
    prefix of `marker` - i.e. how many trailing bytes might be the start
    of `marker` continuing into the next chunk."""
    for length in range(min(len(marker) - 1, end - pos), 0, -1):
        if data[end - length:end] == marker[:length]:
            return length
    return 0


def _entity_holdback_len(data: bytes, pos: int, end: int, max_holdback: int) -> int:
    amp = data.rfind(b"&", pos, end)
    if amp == -1:
        return 0
    tail_len = end - amp
    if tail_len <= max_holdback and b";" not in data[amp:end]:
        return tail_len
    return 0


class _EntitySanitizingReader:
    """Wraps a binary file-like object, repairing bare `&` and unescaped
    HTML named entities in text content before the XML parser sees them.

    Content inside `<![CDATA[ ... ]]>` sections is passed through
    untouched, since XML never entity-parses CDATA in the first place -
    rewriting an "&" there would corrupt it rather than fix anything.
    Markers and entities can straddle two `read()` calls, so a partial
    match at the end of a chunk is held back and retried once more data
    arrives instead of being judged prematurely.
    """

    _MAX_ENTITY_HOLDBACK = 12  # longer than any entity name/codepoint we know

    def __init__(self, source: BinaryIO) -> None:
        self._source = source
        self._pending = b""
        self._in_cdata = False

    def read(self, size: int) -> bytes:
        new = self._source.read(size)
        data = self._pending + new
        self._pending = b""
        if not data:
            return b""
        return self._process(data, at_eof=not new)

    def _process(self, data: bytes, at_eof: bool) -> bytes:
        out = bytearray()
        pos = 0
        n = len(data)

        while pos < n:
            if self._in_cdata:
                end = data.find(_CDATA_END, pos)
                if end == -1:
                    if at_eof:
                        out += data[pos:]
                    else:
                        # A trailing run of "]" could be the start of "]]>".
                        keep = 0
                        while keep < 2 and keep < n - pos and data[n - 1 - keep] == 0x5D:
                            keep += 1
                        out += data[pos:n - keep]
                        self._pending = data[n - keep:]
                    pos = n
                else:
                    out += data[pos:end + len(_CDATA_END)]
                    self._in_cdata = False
                    pos = end + len(_CDATA_END)
            else:
                start = data.find(_CDATA_START, pos)
                if start == -1:
                    if at_eof:
                        out += _AMP_RE.sub(_replace_amp, data[pos:])
                    else:
                        cdata_hold = _partial_prefix_len(data, pos, n, _CDATA_START)
                        entity_hold = _entity_holdback_len(
                            data, pos, n, self._MAX_ENTITY_HOLDBACK
                        )
                        hold = max(cdata_hold, entity_hold)
                        cut = n - hold
                        out += _AMP_RE.sub(_replace_amp, data[pos:cut])
                        self._pending = data[cut:]
                    pos = n
                else:
                    out += _AMP_RE.sub(_replace_amp, data[pos:start])
                    out += data[start:start + len(_CDATA_START)]
                    self._in_cdata = True
                    pos = start + len(_CDATA_START)

        return bytes(out)


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

    Bare `&` and unescaped HTML named entities (`&nbsp;`, `&mdash;`, ...)
    inside text content are repaired before parsing, since real feeds ship
    plenty of both and a strict XML parser would otherwise abort on the
    first one.
    """
    parser = xml.sax.make_parser()
    # Don't fetch external entities/DTDs referenced by the feed - a
    # malicious or misconfigured feed shouldn't cause us to make network
    # or filesystem requests while parsing.
    parser.setFeature(xml.sax.handler.feature_external_ges, False)
    parser.setFeature(xml.sax.handler.feature_external_pes, False)

    handler = _ItemHandler()
    parser.setContentHandler(handler)

    reader = _EntitySanitizingReader(source)
    while True:
        chunk = reader.read(chunk_size)
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
