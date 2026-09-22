"""
Fetch the guideline corpus from its publishers and write it to
app/retrieval/corpus/documents/ as heading-preserving Markdown.

This script is the ONLY part of the retrieval stack that touches the
network. It is run by hand when the corpus is refreshed; the build
(app/retrieval/build.py), the retriever and the tests all read the
committed Markdown and never fetch anything. That split is deliberate: a
corpus re-fetched at build time is not a corpus, it is a dependency on
twenty-eight web servers agreeing with each other on any given morning.

Usage:
    python scripts/fetch_guidelines.py            # fetch and write
    python scripts/fetch_guidelines.py --probe    # fetch, print the date
                                                  # candidates, write nothing
    python scripts/fetch_guidelines.py --only cdc-cholesterol

Every fetch is recorded in app/retrieval/corpus/fetch_manifest.json with
the URL, the HTTP status, the retrieval date and the sha256 of both the
raw bytes and the extracted Markdown, so a corpus file can always be tied
back to what was actually served.

No PHI passes through here. The corpus is published public-domain
guidance, and the only thing this script sends anywhere is an HTTP GET for
a public URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.corpus_meta import CORPUS_DIR, DOCUMENTS_DIR, load_sources  # noqa: E402

#: Honest, and deliberately version-free: cdc.gov's edge returns 403 for a
#: User-Agent carrying a `name/1.0` token, which is what a first attempt at
#: a polite identifying UA looks like. Verified by bisecting the header.
USER_AGENT = (
    "Mozilla/5.0 (clinical-copilot guideline corpus fetcher; research use; "
    "contact repository owner)"
)

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

#: Politeness delay between requests.
FETCH_DELAY_SECONDS = 1.0

MANIFEST_PATH = CORPUS_DIR / "fetch_manifest.json"

# --------------------------------------------------------------------------
# HTML -> Markdown
# --------------------------------------------------------------------------

#: Elements with no end tag. Tracking them on the tag stack is what breaks a
#: naive push-on-start/pop-on-end parser: one `<img>` inside the content root
#: and every subsequent `</div>` pops one frame too many, which silently ends
#: the content root early and empties the document.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Never emit anything inside these. `nav`/`header`/`footer`/`aside` are the
#: site chrome every one of these publishers wraps its content in.
SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        "select",
        "option",
        "textarea",
        "svg",
        "iframe",
        "template",
    }
)

#: Tags whose class is never treated as a chrome marker. uspstf.org ships
#: `<body class="... navbar-is-static-top ...">` and cdc.gov wraps its
#: article in `<div class="cdc-page-content ... syndicate ...">`; a
#: substring rule that fires on a page's own root element deletes the page.
NEVER_CHROME_TAGS = frozenset({"html", "body", "main", "article"})

#: Substrings in a `class` or `id` that mark chrome these publishers do not
#: wrap in a semantic tag. Matched case-insensitively. Kept narrow on
#: purpose: every entry here was checked against all twenty-eight pages,
#: because a substring that also appears in a content wrapper silently
#: empties that document rather than failing loudly.
SKIP_CLASS_SUBSTRINGS = (
    "breadcrumb",
    "skip-link",
    "skipnav",
    "visually-hidden",
    "sr-only",
    "screen-reader",
    "social-media",
    "sharethis",
    "addthis",
    "back-to-top",
    "site-alert",
    "usa-banner",
    "cookie",
    "usa-modal",
    "glossary",
    "page-last-reviewed",
    "newsletter-signup",
    "pagination",
    "main-navigation",
    "sidenav",
    "site-footer",
    "site-header",
)

BLOCK_TAGS = frozenset(
    {
        "p",
        "li",
        "dt",
        "dd",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "td",
        "th",
        "div",
        "section",
        "article",
        "figcaption",
        "summary",
        "caption",
    }
)

HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: Content roots, tightest first. A page with none of them falls back to the
#: whole document, which is noisier but never empty.
ROOT_PREFERENCE = ("main", "article")

#: Blocks carrying a rights notice are dropped from the corpus text.
#:
#: Two real cases, both found by `scripts/audit_corpus_licensing.py`:
#: nhlbi.nih.gov captions its embedded animations "Medical Animation
#: Copyright (c) Nucleus Medical Media, All rights reserved" — a third
#: party's notice for a video this corpus does not carry — and the JAMA-
#: published USPSTF statements carry the AMA/US-Government joint copyright
#: in a paragraph of publication metadata.
#:
#: Neither is clinical guidance, and leaving either in means a retrieval
#: query can surface "All rights reserved" as evidence and a citation can
#: quote it back to a clinician. The licence itself is not being discarded:
#: it is recorded per source in `corpus/sources.yaml`, which is where a
#: reviewer looks for it.
RIGHTS_NOTICE = re.compile(
    r"all rights reserved|medical animation copyright|\(c\)\s*\d{4}\s+AMA",
    re.IGNORECASE,
)

#: Below this many blocks, a content root is assumed to be a decorative
#: wrapper rather than the article, and the next root is tried.
MIN_BLOCKS_FOR_ROOT = 8


@dataclass(slots=True)
class _Frame:
    tag: str
    root: str | None
    skip: bool


@dataclass(slots=True)
class _Block:
    level: int  # 0 for body text
    text: str
    is_list_item: bool
    roots: frozenset[str]


class _Extractor(HTMLParser):
    """
    Collect block-level text, remembering for each block which semantic
    roots it sat inside so the caller can pick the tightest root that
    actually holds the article.

    The tag stack resynchronises on a mismatched end tag rather than
    popping blindly, because every one of these pages has at least one
    unclosed `<p>` or `<li>` in it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self.metas: dict[str, str] = {}
        self.title: str | None = None
        self._stack: list[_Frame] = []
        self._buf: list[str] = []
        self._block_tag: str | None = None
        self._in_title = False

    # -- derived state ---------------------------------------------------
    @property
    def _in_skip(self) -> bool:
        return any(frame.skip for frame in self._stack)

    @property
    def _roots(self) -> frozenset[str]:
        return frozenset(frame.root for frame in self._stack if frame.root)

    @property
    def _in_list_item(self) -> bool:
        return any(frame.tag == "li" for frame in self._stack)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _attr(attrs: list[tuple[str, str | None]], name: str) -> str:
        for key, value in attrs:
            if key == name and value:
                return value
        return ""

    def _is_chrome(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in NEVER_CHROME_TAGS:
            return False
        blob = f"{self._attr(attrs, 'class')} {self._attr(attrs, 'id')}".lower()
        return any(marker in blob for marker in SKIP_CLASS_SUBSTRINGS)

    def _flush(self) -> None:
        text = _normalise_ws("".join(self._buf))
        self._buf.clear()
        tag, self._block_tag = self._block_tag, None
        if not text or tag is None:
            return
        self.blocks.append(
            _Block(
                level=int(tag[1]) if tag in HEADING_TAGS else 0,
                text=text,
                is_list_item=self._in_list_item,
                roots=self._roots,
            )
        )

    # -- HTMLParser API --------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            name = (
                self._attr(attrs, "name")
                or self._attr(attrs, "property")
                or self._attr(attrs, "itemprop")
            )
            content = self._attr(attrs, "content")
            if name and content:
                self.metas.setdefault(name.lower(), content)
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "br":
            self._buf.append(" ")
            return
        if tag in VOID_TAGS:
            return

        root: str | None = None
        if tag == "main" or self._attr(attrs, "role") == "main":
            root = "main"
        elif tag == "article":
            root = "article"

        skip = tag in SKIP_TAGS or self._is_chrome(tag, attrs)
        was_skipping = self._in_skip

        if not was_skipping and tag in BLOCK_TAGS:
            self._flush()

        self._stack.append(_Frame(tag=tag, root=root, skip=skip))

        if not self._in_skip and tag in BLOCK_TAGS:
            self._block_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in VOID_TAGS:
            return

        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == tag:
                if not self._in_skip and tag in BLOCK_TAGS:
                    self._flush()
                del self._stack[index:]
                return
        # No matching open tag: stray close, ignore it.

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = _normalise_ws((self.title or "") + data) or None
            return
        if self._in_skip:
            return
        self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


_WS = re.compile(r"\s+")


def _normalise_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def html_to_markdown(html: str) -> tuple[str, dict[str, str]]:
    """Return (markdown, meta tags) for one page."""
    parser = _Extractor()
    parser.feed(html)
    parser.close()

    blocks = parser.blocks
    for root in ROOT_PREFERENCE:
        scoped = [b for b in parser.blocks if root in b.roots]
        if len(scoped) >= MIN_BLOCKS_FOR_ROOT:
            blocks = scoped
            break

    lines: list[str] = []
    recent: list[str] = []
    for block in blocks:
        if len(block.text) < 2:
            continue
        if RIGHTS_NOTICE.search(block.text):
            continue
        # Site chrome repeats; body text does not repeat verbatim inside a
        # four-block window.
        if block.text in recent[-4:]:
            continue
        recent.append(block.text)
        if block.level:
            lines += ["", f"{'#' * block.level} {block.text}", ""]
        elif block.is_list_item:
            lines.append(f"- {block.text}")
        else:
            lines += ["", block.text, ""]

    markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return (markdown + "\n" if markdown else ""), parser.metas


# --------------------------------------------------------------------------
# publication-date candidates
# --------------------------------------------------------------------------

#: Meta names/properties/itemprops that carry a date on at least one of the
#: six publishers in this corpus.
DATE_META_KEYS = (
    "cdc:last_reviewed",
    "cdc:last_updated",
    "cdc:first_published",
    "lastreviewed",
    "citation_date",
    "article:modified_time",
    "article:published_time",
    "dcterms.modified",
    "dc.date",
    "og:updated_time",
)

#: Date shapes in page text. Each publisher stamps its pages differently and
#: none of them agree, hence one pattern per shape rather than one grand one.
DATE_TEXT_PATTERNS = (
    # USPSTF: <h4 data-qa="pubdate" class="pubdate">August    24, 2021</h4>
    re.compile(r'data-qa="pubdate"[^>]*>\s*([^<]{4,40}?)\s*<', re.IGNORECASE),
    # NIDDK: <div ...>Last Reviewed April 2018</div>
    re.compile(r"Last\s+Reviewed\s*:?\s*([A-Z][a-z]+\s+(?:\d{1,2},\s*)?\d{4})", re.IGNORECASE),
    # NHLBI: Last updated on <time datetime="2024-04-17T12:00:00Z">
    re.compile(r'Last\s+updated\s+on\s*<time[^>]*datetime="([^"]{4,30})"', re.IGNORECASE),
    # MedlinePlus / NIA: plain "Last updated: March 5, 2023"
    re.compile(
        r"Last\s+(?:updated|modified|reviewed)\s*:?\s*"
        r"(?:<[^>]+>\s*){0,3}([A-Z][a-z]+\s+\d{1,2},\s*\d{4})",
        re.IGNORECASE,
    ),
    # Bare <time datetime="..."> anywhere, as a last resort.
    re.compile(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', re.IGNORECASE),
)


def date_candidates(html: str, metas: dict[str, str]) -> dict[str, str]:
    """Every date this page offers, labelled by where it came from."""
    found = {key: metas[key] for key in DATE_META_KEYS if key in metas}
    for index, pattern in enumerate(DATE_TEXT_PATTERNS):
        match = pattern.search(html)
        if match:
            found[f"text[{index}]"] = _normalise_ws(match.group(1))
    return found


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------


def fetch(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the guideline corpus.")
    ap.add_argument("--probe", action="store_true", help="print date candidates, write nothing")
    ap.add_argument("--only", action="append", default=[], help="fetch only these source ids")
    args = ap.parse_args(argv)

    sources = load_sources()
    if args.only:
        wanted = set(args.only)
        sources = [s for s in sources if s.guideline_id in wanted]
        missing = wanted - {s.guideline_id for s in sources}
        if missing:
            print(f"unknown source ids: {sorted(missing)}", file=sys.stderr)
            return 2

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    if MANIFEST_PATH.exists() and not args.only:
        manifest = {}
    elif MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    failures = 0
    for index, source in enumerate(sources):
        if index:
            time.sleep(FETCH_DELAY_SECONDS)
        parts: list[str] = []
        first_html = ""
        first_metas: dict[str, str] = {}
        hashes = hashlib.sha256()
        failed = False
        for part_index, url in enumerate(source.all_urls):
            if part_index:
                time.sleep(FETCH_DELAY_SECONDS)
            status, raw = fetch(url)
            if status != 200:
                print(f"FAIL {status} {source.guideline_id} {url}", file=sys.stderr)
                failed = True
                break
            hashes.update(raw)
            html = raw.decode("utf-8", errors="replace")
            markdown, metas = html_to_markdown(html)
            if not markdown:
                print(f"FAIL empty extraction: {source.guideline_id} {url}", file=sys.stderr)
                failed = True
                break
            if not part_index:
                first_html, first_metas = html, metas
            parts.append(markdown)
        if failed:
            failures += 1
            continue

        joined = "\n\n".join(parts)
        body = joined if joined.startswith("# ") else f"# {source.title}\n\n{joined}"

        if args.probe:
            print(
                f"\n--- {source.guideline_id}  ({len(body)} chars, "
                f"{body.count(chr(10) + '#')} headings, {len(parts)} part(s)) ---"
            )
            for label, value in date_candidates(first_html, first_metas).items():
                print(f"    {label:<24} {value}")
            continue

        path = DOCUMENTS_DIR / f"{source.guideline_id}.md"
        path.write_text(body, encoding="utf-8")
        manifest[source.guideline_id] = {
            "urls": source.all_urls,
            "http_status": 200,
            "retrieved": source.retrieved.isoformat(),
            "raw_sha256": hashes.hexdigest(),
            "markdown_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "markdown_bytes": len(body.encode("utf-8")),
        }
        print(f"ok   {source.guideline_id:<42} {len(body):>7} chars -> {path.name}")

    if not args.probe:
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"\nmanifest: {MANIFEST_PATH.name} ({len(manifest)} entries)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
