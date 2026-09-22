"""
Licensing audit: re-fetch every corpus page and look for anything that is
not a US federal government work.

`sources.yaml` asserts `us-gov-public-domain` for all 28 documents. That
assertion is about the *publisher*, and a publisher can host third-party
material: MedlinePlus is public domain, but the A.D.A.M. Medical
Encyclopedia it hosts under `/ency/` is copyrighted by A.D.A.M. Inc. and
the drug pages under `/druginfo/` are copyrighted by ASHP. A page can also
embed a licensed figure or animation with a "used with permission" notice.
None of that is visible in the extracted Markdown once the chrome is
stripped, which is exactly why this reads the raw HTML.

    python scripts/audit_corpus_licensing.py            # audit everything
    python scripts/audit_corpus_licensing.py --offline  # committed Markdown only

This is a **finding tool, not a verdict tool.** Every hit is printed with
its context for a person to judge; a page carrying "© 2026 Some Vendor"
next to an embedded video is a different problem from a page whose body
text is licensed. What it must never do is pass silently.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.corpus_meta import load_registry  # noqa: E402

# Imported for its fetch()/headers so the audit sees what the fetcher saw.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_guidelines import fetch  # noqa: E402

#: (severity, label, pattern). `blocker` means the corpus may not ship the
#: page without a human decision; `notice` means look at it.
PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("blocker", "A.D.A.M. encyclopedia", re.compile(r"A\.?D\.?A\.?M\.?[,.\s]", re.IGNORECASE)),
    (
        "blocker",
        "ASHP drug information",
        re.compile(r"\bASHP\b|American Society of Health-System", re.IGNORECASE),
    ),
    (
        "blocker",
        "used with permission",
        re.compile(r"(used|reprinted|reproduced)\s+with\s+permission", re.IGNORECASE),
    ),
    ("blocker", "all rights reserved", re.compile(r"all\s+rights\s+reserved", re.IGNORECASE)),
    ("notice", "copyright symbol", re.compile(r"©|&copy;|&#169;")),
    ("notice", "the word copyright", re.compile(r"\bcopyright\b", re.IGNORECASE)),
    ("notice", "licensed/licensor", re.compile(r"\blicens(ed|or|ing)\b", re.IGNORECASE)),
)

#: Site chrome that matches a pattern on every page of a publisher and says
#: nothing about the content. Each entry was read before it was added.
BENIGN = (
    # MedlinePlus links its own copyright policy page in the site footer.
    "medlineplus.gov/about/using/usingcontent",
    "about/using/usingcontent",
    # NIH/NLM footer boilerplate naming the public-domain policy itself.
    "copyright infringement",
    # The literal id of a CDC footer anchor.
    "cdc-copyright",
    # cdc.gov/medication-safety lists outbound resources by publisher. The
    # line "Safe Medication, American Society of Health-System Pharmacists
    # (ASHP)" is a CDC-written link title naming an organisation; it is not
    # ASHP-authored content, which is what the ASHP pattern is looking for
    # (medlineplus.gov/druginfo/ pages). Narrow on purpose: the exemption
    # matches the link title, not the word "ASHP".
    "safe medication, american society of health-system pharmacists",
    "consumermedsafety.org",
)

TAG = re.compile(r"<[^>]+>")


def context(haystack: str, match: re.Match[str], width: int = 110) -> str:
    start = max(0, match.start() - width)
    end = min(len(haystack), match.end() + width)
    snippet = TAG.sub(" ", haystack[start:end])
    return " ".join(snippet.split())


def audit_text(label: str, text: str) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    for severity, name, pattern in PATTERNS:
        for match in pattern.finditer(text):
            snippet = context(text, match)
            if any(benign in snippet.lower() for benign in BENIGN):
                continue
            findings.append((severity, name, snippet))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit the corpus for non-public-domain content.")
    ap.add_argument("--offline", action="store_true", help="audit the committed Markdown only")
    args = ap.parse_args(argv)

    registry = load_registry()
    blockers = 0
    notices = 0

    for source in registry.sources:
        findings: list[tuple[str, str, str]] = []
        if args.offline:
            findings = audit_text(
                source.guideline_id, source.document_path.read_text(encoding="utf-8")
            )
        else:
            for url in source.all_urls:
                status, raw = fetch(url)
                if status != 200:
                    print(f"FETCH-FAIL {status} {source.guideline_id} {url}", file=sys.stderr)
                    continue
                findings.extend(audit_text(url, raw.decode("utf-8", errors="replace")))

        if not findings:
            print(f"clean     {source.guideline_id}")
            continue

        # One line per (severity, pattern); the snippets repeat otherwise.
        seen: set[tuple[str, str]] = set()
        print(f"\n### {source.guideline_id}  ({source.url})")
        for severity, name, snippet in findings:
            if (severity, name) in seen:
                continue
            seen.add((severity, name))
            marker = "BLOCKER" if severity == "blocker" else "notice "
            print(f"  {marker} {name}")
            print(f"          …{snippet}…")
            if severity == "blocker":
                blockers += 1
            else:
                notices += 1

    print(f"\n{blockers} blocker(s), {notices} notice(s) across {len(registry.sources)} sources")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
