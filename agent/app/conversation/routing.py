"""
Rule-driven routing for a conversation turn — what to refuse, what to
retrieve, and which lab results the turn's bounded set holds.

Everything here is plain pattern matching over the question's wording and
set arithmetic over the conversation's verified context. No model is
consulted: the model never chooses what is fetched (ARCHITECTURE.md §3),
and it is never asked whether a question is safe to answer. It drafts
claims only after this module has fixed the turn's record set, and those
claims are then verified against exactly that set.

Three decisions, in order:

1. Refuse. A question that names a part of the chart outside the five
   tool-scope resource types (vitals, notes, procedures, immunizations,
   imaging, family/social history, scheduling, demographics/billing, other
   patients), or that asks for a judgment, comparison or trend — an answer
   no single record states and the verifier therefore cannot check
   (ARCHITECTURE.md §5) — is refused before any chart search or model call.
2. Retrieve. Condition, MedicationRequest and AllergyIntolerance are read
   on every turn. Lab wording adds Observation; visit wording ("last
   visit", "since", "last time") adds Encounter; and a type an earlier
   turn verified claims on is carried, so "when was that result taken?"
   can be re-cited against fresh records. Those are the only retrievals
   that exist.
3. Narrow labs. One chart on the deployment host has 2,203 lab results
   (verified 2026-09-21 against the Vultr host: 29 of 52 patients hold
   more lab results than LAB_RESULTS_GENERAL, 16 hold a single LOINC code
   over LAB_RESULTS_PER_CODE). The turn's Observation set is the few most
   recent results per LOINC code the question's wording (or the carried
   context) names, or the most recent results overall for a general lab
   question. The cap is disclosed twice and silent neither time: to the
   physician in the answer's closing line, and to the model in
   <retrieval_scope>, which states how many records were left out.

The rules are wording heuristics, and they err toward refusing: a wrongly
refused question costs the physician a rephrase, and a question they
wrongly let through still meets a model that holds only this turn's
records and a verifier that passes nothing else. Rule ids are logged;
question text never is.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.conversation.lab_codes import analyte_matches
from app.models.conversation import RefusalCategory, RetrievalRule
from app.models.fhir_resources import FhirResourceType, Observation

# Of the results a lab question's codes match, how many of the most recent
# are kept per code, and in total; and how many for a general lab question.
LAB_RESULTS_PER_CODE = 8
LAB_RESULTS_MAX = 160
LAB_RESULTS_GENERAL = 40

# Encounter is a dated anchor only (app/models/fhir_resources.py); the ten
# most recent are enough to answer "when was she last seen".
ENCOUNTER_COUNT = 10

OUT_OF_SCOPE = frozenset({
    RefusalCategory.VITALS,
    RefusalCategory.CLINICAL_NOTES,
    RefusalCategory.PROCEDURES,
    RefusalCategory.IMMUNIZATIONS,
    RefusalCategory.IMAGING,
    RefusalCategory.FAMILY_SOCIAL_HISTORY,
    RefusalCategory.SCHEDULING,
    RefusalCategory.DEMOGRAPHICS_BILLING,
    RefusalCategory.OTHER_PATIENTS,
})


def _rule(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_NOT_A_DRUG = r"(?!\s+(medications?|medicines?|meds?|drugs?|pills?|tablets?))"

_REFUSAL_RULES: tuple[tuple[RefusalCategory, re.Pattern[str]], ...] = (
    (
        RefusalCategory.OTHER_PATIENTS,
        _rule(
            r"\b((other|another|different|previous|next|last|all( of)?( my| the)?) patients?"
            r"|patient (list|panel)|my (panel|patients)|someone else|anyone else)\b"
        ),
    ),
    (
        RefusalCategory.VITALS,
        _rule(
            r"\b(vitals?( signs?)?|blood pressure" + _NOT_A_DRUG + r"|bp" + _NOT_A_DRUG
            + r"|heart rate|pulse|resp(iratory)? rate|temperature|weight" + _NOT_A_DRUG
            + r"|bmi|body mass|height|spo2|pulse ox(imetry)?)\b"
        ),
    ),
    (
        RefusalCategory.CLINICAL_NOTES,
        _rule(
            r"\b((progress|clinic(al)?|visit|office|soap|nursing|discharge|consult(ation)?) notes?"
            r"|notes|hpi|documentation|dictation|discharge summar(y|ies)|assessment and plan)\b"
        ),
    ),
    (
        RefusalCategory.PROCEDURES,
        _rule(r"\b(procedures?|surger(y|ies)|surgical|operations?|colonoscop(y|ies)|biops(y|ies))\b"),
    ),
    (
        RefusalCategory.IMMUNIZATIONS,
        _rule(r"\b(immuni[sz]\w*|vaccin\w*|flu shot|shots|boosters?)\b"),
    ),
    (
        RefusalCategory.IMAGING,
        _rule(
            r"\b(imaging|x-?rays?|mri|ct scan|cat scan|ultrasound|echo(cardiogram)?|mammogram"
            r"|radiolog\w*|ekg|ecg)\b"
        ),
    ),
    (
        RefusalCategory.FAMILY_SOCIAL_HISTORY,
        _rule(
            r"\b(family (history|hx)|social (history|hx)|smok\w*|tobacco|alcohol|drinks?|drinking"
            r"|drug use|occupation|employment|marital|living situation)\b"
        ),
    ),
    (
        RefusalCategory.SCHEDULING,
        _rule(
            r"\b((next|upcoming|future|scheduled) (appointments?|visits?|encounters?)"
            r"|schedul(e|ed|ing)|booked)\b"
        ),
    ),
    (
        RefusalCategory.DEMOGRAPHICS_BILLING,
        _rule(
            r"\b(insurance|insurer|copay|billing|address|phone( number)?|email|ssn|social security"
            r"|emergency contact|date of birth|dob|birthday|how old|age)\b"
        ),
    ),
    (
        RefusalCategory.CHANGE_DETECTION,
        _rule(
            r"\b((what|anything|something|what else)('s| is| has| have)? (changed|new|different)"
            r"|has anything changed|any changes|summari[sz]e the changes)\b"
        ),
    ),
    (
        RefusalCategory.TREND_OR_COMPARISON,
        _rule(
            r"\b(worsen\w*|worse|improv\w*|better|deteriorat\w*|declin\w*|trend\w*|trajectory"
            r"|over time|progress(ing|ed|ion)|going (up|down)|(gone|went|come|came) (up|down)"
            r"|increas\w*|decreas\w*|rising|falling|dropp(ed|ing)|fluctuat\w*"
            r"|stable(?!\s+angina)|compar\w*|(higher|lower|more|less|fewer) than|versus|vs"
            r"|difference between)\b"
        ),
    ),
    (
        RefusalCategory.CLINICAL_JUDGMENT,
        _rule(
            r"\b(should (i|we|she|he|they)|do you (think|recommend|suggest)|what do you think"
            r"|recommend\w*|advis\w*|suggest\w*|your (opinion|assessment|impression)|interpret\w*"
            r"|safe to|is (it|this|that) (safe|appropriate)|appropriate|contraindicat\w*"
            r"|interact\w*|at (goal|target)|(well|poorly)[- ]controlled|uncontrolled|controlled"
            r"|(in|out of|within) (the )?(normal )?(range|limits)|reference range"
            r"|(normal|abnormal) (range|values?|results?|levels?)|risk of|likely|prognosis"
            r"|differential|diagnos(e|ing)|concern(ing|ed)?|worr(y|ied|ying|isome))\b"
        ),
    ),
    (
        # "Is her potassium high?", "Are those normal?" — an adjective that
        # characterizes a value, used as the predicate. "When was her high
        # cholesterol diagnosed?" is a record's name and does not match:
        # the adjective must end the clause.
        RefusalCategory.CLINICAL_JUDGMENT,
        _rule(
            r"\b(is|are|was|were|looks?|looking|seems?)\b[^.?!]{0,60}?"
            r"\b(too )?(high|low|elevated|normal|abnormal|fine|ok|okay|good|bad)\b"
            r"(?=\s*([?.!,]|$|or\b|for\b|enough\b|given\b|considering\b))"
        ),
    ),
)

_LAB_WORDING = _rule(
    r"\b(labs?|lab\s?work|laborator(y|ies)|blood\s?work|bloods|test results?|results?|panels?"
    r"|levels?|values?|scores?)\b"
)
_VISIT_WORDING = _rule(
    r"\b(visits?|encounters?|appointments?|seen|last time|came in|come in|since|admission"
    r"|admitted|hospitali[sz]\w*)\b"
)

_EXPANDABLE = (FhirResourceType.OBSERVATION, FhirResourceType.ENCOUNTER)


@dataclass(frozen=True)
class RoutingDecision:
    """
    `refusals` non-empty means nothing is retrieved and no model is called.
    `rule_ids` is what the turn's routing event logs.
    """

    refusals: tuple[RefusalCategory, ...]
    expansions: dict[FhirResourceType, RetrievalRule]
    lab_codes: frozenset[str]
    rule_ids: tuple[str, ...]

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    @property
    def refused_out_of_scope(self) -> bool:
        return any(category in OUT_OF_SCOPE for category in self.refusals)


def route(question: str, context_types: frozenset[FhirResourceType]) -> RoutingDecision:
    """
    `context_types`: the resource types this conversation's earlier turns
    verified claims on (app/conversation/state.py).
    """
    refusals = tuple(
        dict.fromkeys(
            category for category, pattern in _REFUSAL_RULES if pattern.search(question)
        )
    )
    if refusals:
        return RoutingDecision(
            refusals=refusals,
            expansions={},
            lab_codes=frozenset(),
            rule_ids=tuple(f"refuse:{category.value}" for category in refusals),
        )

    rule_ids: list[str] = []
    expansions: dict[FhirResourceType, RetrievalRule] = {}
    analytes = analyte_matches(question)
    if analytes or _LAB_WORDING.search(question):
        expansions[FhirResourceType.OBSERVATION] = RetrievalRule.QUESTION_RULE
        rule_ids.append("retrieve:labs")
        rule_ids.extend(f"labs:{rule_id}" for rule_id, _ in analytes)
    if _VISIT_WORDING.search(question):
        expansions[FhirResourceType.ENCOUNTER] = RetrievalRule.QUESTION_RULE
        rule_ids.append("retrieve:visits")
    for resource_type in _EXPANDABLE:
        if resource_type in context_types and resource_type not in expansions:
            expansions[resource_type] = RetrievalRule.CONTEXT_CARRY
            rule_ids.append(f"carry:{resource_type.value}")

    return RoutingDecision(
        refusals=(),
        expansions=expansions,
        lab_codes=frozenset(code for _, codes in analytes for code in codes),
        rule_ids=tuple(rule_ids),
    )


@dataclass(frozen=True)
class LabScope:
    """
    How the turn's lab set was cut from what FHIR returned. Rendered to the
    physician (app/graph/conversation.py:_lab_part) and stated to the model
    (app/reasoning/draft_conversation_turn.py:serialize_retrieval_scope).

    `omitted` is every retrieved result the turn's set does not hold, for
    any reason — over a cap, or of a test the question is not about.
    `truncated_codes` is the narrower, sharper fact: one entry per LOINC
    code the question IS about whose older results were cut by
    LAB_RESULTS_PER_CODE, as (code, kept, total). A model told only
    `considered` cannot tell "8 results because the chart holds 8" from
    "8 of 83", and so cannot honestly set coverage on "what were her
    creatinine results".
    """

    retrieved: int
    considered: int
    codes: tuple[str, ...]
    omitted: int = 0
    truncated_codes: tuple[tuple[str, int, int], ...] = ()


def _newest_first(observations: Sequence[Observation]) -> list[Observation]:
    # An absent date is datetime.min (app/fhir/client.py), so it sorts
    # last. Aware and naive datetimes do not compare; the host returns
    # aware ones only, and a naive one is ordered by its clock reading.
    def key(observation: Observation) -> tuple[datetime, str]:
        return observation.effective_date.replace(tzinfo=None), observation.source_id

    return sorted(observations, key=key, reverse=True)


def narrow_observations(
    observations: Sequence[Observation],
    lab_codes: frozenset[str],
    context_source_ids: frozenset[str],
) -> tuple[list[Observation], LabScope]:
    """
    The lab results the turn's bounded set holds, newest first.

    `lab_codes`: from the question's wording (RoutingDecision.lab_codes).
    `context_source_ids`: Observation ids an earlier turn verified. Those
    records, and the other results sharing their LOINC codes, are kept, so
    "and the one before that?" has something to cite.
    """
    ordered = _newest_first(observations)
    cited = [o for o in ordered if o.source_id in context_source_ids]
    codes = set(lab_codes) | {o.loinc_code for o in cited}

    # Per-code totals over everything retrieved, taken before any cut, so
    # `truncated_codes` below can state "8 of 83" rather than just "8".
    totals: dict[str, int] = {}
    for observation in ordered:
        totals[observation.loinc_code] = totals.get(observation.loinc_code, 0) + 1

    truncated: list[tuple[str, int, int]] = []
    if not codes:
        kept = ordered[:LAB_RESULTS_GENERAL]
    else:
        kept_ids: dict[str, Observation] = {}
        per_code: dict[str, int] = {}
        for observation in ordered:
            if observation.loinc_code not in codes:
                continue
            seen = per_code.get(observation.loinc_code, 0)
            if seen < LAB_RESULTS_PER_CODE:
                per_code[observation.loinc_code] = seen + 1
                kept_ids[observation.source_id] = observation
        kept = list(kept_ids.values())[:LAB_RESULTS_MAX]
        present = {o.source_id for o in kept}
        kept.extend(o for o in cited if o.source_id not in present)
        kept = _newest_first(kept)
        # Counted from `kept`, not from `per_code`: LAB_RESULTS_MAX can cut
        # a code below its per-code allowance, and a context-carried record
        # can push it back up. What the model is told must be what the model
        # is given.
        final_per_code: dict[str, int] = {}
        for observation in kept:
            final_per_code[observation.loinc_code] = (
                final_per_code.get(observation.loinc_code, 0) + 1
            )
        truncated = [
            (code, final_per_code.get(code, 0), totals[code])
            for code in sorted(codes)
            if totals.get(code, 0) > final_per_code.get(code, 0)
        ]

    return kept, LabScope(
        retrieved=len(observations),
        considered=len(kept),
        codes=tuple(sorted(codes)),
        omitted=len(observations) - len(kept),
        truncated_codes=tuple(truncated),
    )
