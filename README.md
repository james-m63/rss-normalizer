# rss-normalizer

Most RSS feeds in the wild are a mess: dates in half a dozen formats
(`pubDate` is supposed to be RFC 822 but plenty of feeds ship ISO 8601 or
something homegrown), stray whitespace and line breaks inside titles,
missing fields, and occasionally tens of megabytes of `<item>` elements
from a feed that never got trimmed.

This is a small formatter that reads an RSS 2.0 or Atom document and yields
one normalized item at a time:

- `pubDate` parsed and rewritten as UTC ISO 8601, original kept alongside
  in case the parse was wrong or you need it
- whitespace inside text fields collapsed and trimmed
- missing fields come back as `None` instead of blowing up

The part that matters most: it never builds a DOM or holds the whole feed
in memory. It feeds an incremental SAX parser chunk by chunk from whatever
file-like object you give it, and hands you each `<item>` as soon as its
closing tag arrives. A 200MB feed and a 2KB feed use the same amount of
memory to parse.

## Usage

```python
from rssnorm import parse_stream

with open("feed.xml", "rb") as f:
    for item in parse_stream(f):
        print(item.pub_date, item.title)
```

Works the same way against a network response, since `parse_stream` only
needs an object with a `.read(n)` method returning bytes:

```python
import urllib.request
from rssnorm import parse_stream

with urllib.request.urlopen("https://example.com/feed.xml") as resp:
    for item in parse_stream(resp):
        if item.pub_date is None:
            print("could not parse date:", item.raw_pub_date)
        print(item.title, item.link)
```

Each yielded item is a `FeedItem`:

```python
FeedItem(
    title="...",
    link="...",
    description="...",
    guid="...",
    pub_date="2026-09-04T08:00:00Z",   # normalized, or None
    raw_pub_date="Fri, 04 Sep 2026 08:00:00 GMT",  # whatever the feed had
)
```

Atom `<entry>` elements are read the same way and mapped onto the same
`FeedItem` shape: `id` becomes `guid`, `summary` (falling back to `content`)
becomes `description`, and `published` (falling back to `updated`) becomes
`pub_date`. `<link href="...">` is read from the first `alternate` (or
rel-less) link in the entry.

## Current limitations

This is the first working version, not a complete parser:

- feeds with real (non-CDATA) markup inside `<description>` have those tags
  stripped and their text concatenated in - there's no conversion of `<br>`
  to a newline or the like, since the parser only tracks character content
- bare `&` and unescaped HTML named entities (`&nbsp;`, `&mdash;`, ...) in
  text content are repaired before parsing, but a stray unescaped `<` will
  still abort the parse
- no charset sniffing beyond what the XML parser does on its own

## Requirements

Python 3.9+, standard library only.
