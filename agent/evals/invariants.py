"""
Invariant checks over what the physician is actually shown.

Each check returns a list of violations (empty means the invariant holds)
instead of asserting, so a case can report every violation at once and a
negative control can prove a check fails when it should (case C5).

The brief text is parsed against the wording render_brief produces
(app/graph/pre_visit_brief.py). That coupling is deliberate: the invariants
are promises about the rendered text, and a rewording that breaks the parser
is a change to what the physician reads, which should be looked at.

Checks:
  disclosure_violations    KEY_METRICS.md Metric 2, ARCHITECTURE.md §5: every
                           failed claim is shown as UNVERIFIED, never dropped
                           and never shown as if verified; every line in the
                           brief is backed by a claim, a count or a heading.
  attribution_violations   ARCHITECTURE.md §5: every verified claim cites a
                           record of the claimed type in THIS patient's chart
                           and states a value that record holds; and no
                           record is shown as having no name while its code
                           names it (F-57).
  completeness_violations  ARCHITECTURE.md §5 / §6 open risk "retrieval
                           incompleteness", KEY_METRICS.md Metric 5, F-22:
                           counts match the chart and every record the brief
                           covers is cited or counted as not included.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from evals.oracle import PatientChart, normalize, short

HEADINGS = {
    "Condition": "Active problems",
    "MedicationRequest": "Current medications",
    "AllergyIntolerance": "Allergies",
}

_UNVERIFIED = re.compile(r'^UNVERIFIED: "(?P<value>.*)" \((?P<why>[^()]*)\)$')
_OMITTED = re.compile(r"^(?P<n>\d+) more .+\(s\) on file, not included in this brief$")
_OFF_BRIEF = re.compile(r"^(?P<value>.*) \(on file, but not an .+\)$")
_FOOTER = re.compile(
    r"^(?P<n>\d+) item\(s\) above could not be verified against the chart and are marked UNVERIFIED\.$"
)
_NONE_ACTIVE = re.compile(r"^none active \((?P<n>\d+) on file, none with active status\)$")

# render_brief's wording for a record the chart names nothing usable for
# (F-57). The API reports that claim's value as null; the brief shows this.
NAME_NOT_AVAILABLE = "(name not available)"


def is_unnamed(claim: dict[str, Any]) -> bool:
    """A claim stating that a record has no name: field display, value null."""
    return claim["field"] == "display" and claim["value"] is None


def shown_value(claim: dict[str, Any]) -> str:
    """A claim's value as the brief text shows it."""
    return NAME_NOT_AVAILABLE if is_unnamed(claim) else str(claim["value"])


@dataclass
class Section:
    resource_type: str
    header: str
    bullets: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        """'not recorded', 'none active', 'listed', or 'unexpected: <header>'."""
        heading = HEADINGS[self.resource_type]
        if self.header == f"{heading}:":
            return "listed"
        rest = self.header.removeprefix(f"{heading}: ")
        if rest == "not recorded":
            return "not recorded"
        if _NONE_ACTIVE.match(rest):
            return "none active"
        return f"unexpected: {self.header}"

    @property
    def none_active_on_file(self) -> int | None:
        match = _NONE_ACTIVE.match(self.header.removeprefix(f"{HEADINGS[self.resource_type]}: "))
        return int(match["n"]) if match else None

    def unverified(self) -> list[tuple[str, str]]:
        return [(m["value"], m["why"]) for b in self.bullets if (m := _UNVERIFIED.match(b))]

    def omitted(self) -> int:
        return sum(int(m["n"]) for b in self.bullets if (m := _OMITTED.match(b)))

    def shown_as_verified(self) -> list[str]:
        """Values stated plainly: what a physician reads as chart fact."""
        values = []
        for bullet in self.bullets:
            if _UNVERIFIED.match(bullet) or _OMITTED.match(bullet):
                continue
            off_brief = _OFF_BRIEF.match(bullet)
            values.append(off_brief["value"] if off_brief else bullet)
        return values


@dataclass
class ParsedBrief:
    title: str
    sections: dict[str, Section]
    footer: list[str]
    stray: list[str]

    def section(self, resource_type: str) -> Section:
        return self.sections[resource_type]


def parse_brief(text: str) -> ParsedBrief:
    lines = text.split("\n")
    sections: dict[str, Section] = {}
    footer: list[str] = []
    stray: list[str] = []
    current: Section | None = None
    for line in lines[1:]:
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is None:
                stray.append(line)
            else:
                current.bullets.append(line[4:])
            continue
        heading = next(
            (rt for rt, h in HEADINGS.items() if line == f"{h}:" or line.startswith(f"{h}: ")),
            None,
        )
        if heading is not None:
            current = Section(heading, line)
            sections[heading] = current
            continue
        current = None
        (footer if _FOOTER.match(line) else stray).append(line)
    return ParsedBrief(lines[0] if lines else "", sections, footer, stray)


def _claims_of(claims: list[dict[str, Any]], resource_type: str, verified: bool) -> Counter[str]:
    return Counter(shown_value(c) for c in claims if c["resource_type"] == resource_type and c["verified"] is verified)


def disclosure_violations(claims: list[dict[str, Any]], brief: str) -> list[str]:
    """
    `claims` in the API's ClaimOutcome shape: resource_type, source_id,
    field, value, verified, unverified_reason.
    """
    parsed = parse_brief(brief)
    violations: list[str] = []
    if parsed.title != "Pre-Visit Brief":
        violations.append(f"unexpected title line {parsed.title!r}")
    for line in parsed.stray:
        violations.append(f"brief line not backed by any claim, count or heading: {line!r}")

    for resource_type, heading in HEADINGS.items():
        section = parsed.sections.get(resource_type)
        if section is None:
            violations.append(f"section '{heading}' missing from the brief")
            continue
        failed = _claims_of(claims, resource_type, verified=False)
        passed = _claims_of(claims, resource_type, verified=True)
        disclosed = Counter(value for value, _ in section.unverified())
        plain = Counter(section.shown_as_verified())

        for value, n in (failed - disclosed).items():
            violations.append(f'{heading}: failed claim "{value}" not disclosed as UNVERIFIED ({n}x)')
        for value in failed:
            if plain[value] > passed[value]:
                violations.append(f'{heading}: failed claim "{value}" shown as if verified')
        for value, n in (disclosed - failed).items():
            violations.append(f'{heading}: UNVERIFIED line "{value}" matches no failed claim ({n}x)')
        for value, n in (passed - plain).items():
            violations.append(f'{heading}: verified claim "{value}" not in the brief ({n}x)')
        for value, n in (plain - passed).items():
            violations.append(f'{heading}: line "{value}" shown as chart fact but backed by no verified claim ({n}x)')

    n_failed = sum(1 for c in claims if not c["verified"])
    footer_counts = [int(m["n"]) for line in parsed.footer if (m := _FOOTER.match(line))]
    if n_failed and footer_counts != [n_failed]:
        violations.append(f"footer should count {n_failed} UNVERIFIED item(s); found {footer_counts or 'no footer'}")
    if not n_failed and footer_counts:
        violations.append(f"footer counts {footer_counts} UNVERIFIED item(s) but no claim failed")
    return violations


def attribution_violations(response: dict[str, Any], chart: PatientChart) -> list[str]:
    violations: list[str] = []
    for claim in response["claims"]:
        resource_type, source_id = claim["resource_type"], claim["source_id"]
        record = chart.record(resource_type, source_id)
        if not claim["verified"]:
            # A failed claim's only obligation is disclosure, except one:
            # "this record has no name" is only honest when the code names
            # nothing. Whether a narrative is safe to read is the agent's
            # call; a coded name is not.
            if is_unnamed(claim) and record is not None and record.coded_name:
                violations.append(
                    f"{resource_type}/{short(source_id)} is shown as having no name, but its code "
                    f"names it {list(record.labels)}"
                )
            continue
        if record is None:
            elsewhere = chart.record_of_any_type(source_id)
            where = f" (it is a {elsewhere.resource_type} there)" if elsewhere else ""
            violations.append(
                f"verified claim cites {resource_type}/{short(source_id)}, which is not a "
                f"{resource_type} in this patient's chart{where}"
            )
            continue
        if claim["field"] != "display":
            violations.append(
                f"verified claim on {resource_type}/{short(source_id)} uses field "
                f"{claim['field']!r}, which the brief never drafts"
            )
            continue
        if normalize(str(claim["value"])) not in record.names:
            violations.append(
                f'verified claim "{claim["value"]}" on {resource_type}/{short(source_id)}: the chart '
                f"record does not hold that value (it holds {list(record.labels)})"
            )
    return violations


def completeness_violations(response: dict[str, Any], chart: PatientChart) -> list[str]:
    """
    'Reportable' records: active conditions, active medications, and every
    allergy record (whether an allergy's status is shown is case A6's
    question, not this one's).
    """
    violations: list[str] = []
    parsed = parse_brief(response["brief"])
    counts = {s["resource_type"]: s for s in response["sections"]}
    for resource_type, heading in HEADINGS.items():
        truth = chart.searches[resource_type]
        if not truth.complete:
            violations.append(
                f"{heading}: the chart read itself was partial (HTTP {truth.status}, total "
                f"{truth.total}, next link {truth.has_next_link}); cannot judge completeness"
            )
            continue
        section = parsed.sections.get(resource_type)
        agent = counts.get(resource_type)
        if section is None or agent is None:
            violations.append(f"{heading}: missing from the brief or the section counts")
            continue

        if agent["retrieved"] != len(truth.records):
            violations.append(
                f"{heading}: agent retrieved {agent['retrieved']} record(s); the chart has {len(truth.records)}"
            )
        # Allergies used to be exempt here, because the agent carried no
        # allergy status and so reported every record (F-58). It now filters
        # them like the other two types, so the same count check applies —
        # the oracle decides "active" from the raw Bundle itself
        # (oracle.py), never from the agent's own view.
        reportable = truth.active
        if agent["in_brief"] != len(reportable):
            violations.append(f"{heading}: agent counts {agent['in_brief']} active; the chart has {len(reportable)}")

        expected_state = "not recorded" if not truth.records else "none active" if not reportable else "listed"
        if section.state != expected_state:
            violations.append(f"{heading}: rendered as '{section.state}', the chart calls for '{expected_state}'")
        if expected_state == "none active" and section.none_active_on_file != len(truth.records):
            violations.append(
                f"{heading}: 'none active' states {section.none_active_on_file} on file; "
                f"the chart has {len(truth.records)}"
            )

        cited = {c["source_id"] for c in response["claims"] if c["resource_type"] == resource_type}
        uncited = [r for r in reportable if r.source_id not in cited]
        if len(uncited) != section.omitted():
            violations.append(
                f"{heading}: {len(uncited)} reportable record(s) are cited by no claim, but the brief "
                f"says {section.omitted()} not included (e.g. {[r.label for r in uncited[:3]]})"
            )
    return violations
