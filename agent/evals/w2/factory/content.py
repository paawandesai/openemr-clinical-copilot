"""
Content planning: (spec + chart) -> everything the renderers draw.

Separating this from the renderers keeps one rule easy to enforce: the document
matches the chart except where a perturbation says otherwise, and every one of
those differences ends up in the ground truth's ``perturbations`` list with the
chart value, the document value and a sentence saying what the discrepancy is.

Everything here is drawn from seeded streams (``evals.w2.factory.rng``), so a
given spec always plans the same document. Dates are offsets from a fixed
anchor rather than from the clock, for the same reason.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass

from evals.w2.factory.chart import ChartRecord
from evals.w2.factory.groundtruth import PerturbationRecord
from evals.w2.factory.panels import (
    MULTI_PAGE_PANELS,
    PANELS_BY_KEY,
    SINGLE_PAGE_PANELS,
    LabPanel,
    LabTest,
    find_test,
)
from evals.w2.factory.rng import stream
from evals.w2.factory.spec import PerturbationKind, ScenarioSpec, SpecError

#: Documents are dated relative to this fixed anchor, never to the clock —
#: a run in six months must still produce byte-identical output.
ANCHOR_DATE = dt.date(2026, 9, 15)

_LABS = (
    (
        "Northfield Regional Laboratory",
        "1400 Cabot Industrial Way, Suite 210",
        "Peabody, MA 01960",
        "(978) 555-0142",
        "22D0451193",
        "Alan R. Petrosian, MD, PhD",
    ),
    (
        "Bay State Clinical Diagnostics",
        "88 Harborview Drive",
        "Quincy, MA 02169",
        "(617) 555-0188",
        "22D2118740",
        "Nadia Okonjo-Reyes, MD",
    ),
    (
        "Merrimack Valley Pathology Associates",
        "310 River Mill Road",
        "Lowell, MA 01852",
        "(978) 555-0119",
        "22D9042216",
        "Terrence Vuong, DO",
    ),
)

_ORDERING_PROVIDERS = (
    "Priya Raghunathan, MD",
    "Desmond Achebe, MD",
    "Hannah Lindqvist, NP",
    "Marcus Oyelaran, MD",
    "Sofia Bertelli, PA-C",
)

_CLINICS = (
    ("Lynn Family Health Center", "45 Union Street, Lynn, MA 01901", "(781) 555-0164"),
    ("Riverbend Primary Care", "2200 Mill Pond Avenue, Haverhill, MA 01830", "(978) 555-0173"),
    ("Cedar Street Internal Medicine", "17 Cedar Street, Salem, MA 01970", "(978) 555-0121"),
)

_WRONG_NAMES = (
    "Gerald A. Mathers",
    "Rosalind Petrakis",
    "Owen Delacroix-Hart",
    "Bernadette Sowande",
    "Curtis Yamamoto",
)

_FREQUENCIES = (
    "once daily",
    "twice daily",
    "every morning",
    "at bedtime",
    "as needed",
    "three times daily",
)

_ADDED_MEDS = (
    ("Metformin", "500 MG", "twice daily"),
    ("Atorvastatin", "20 MG", "at bedtime"),
    ("Gabapentin", "300 MG", "three times daily"),
    ("Omeprazole", "20 MG", "once daily"),
)

_ADDED_ALLERGIES = (
    ("Sulfa drugs", "rash"),
    ("Latex", "hives"),
    ("Shellfish", "swelling"),
    ("Codeine", "nausea"),
)

_FAMILY_HISTORY_CONDITIONS = (
    ("diabetes", "Diabetes"),
    ("hypertension", "High blood pressure"),
    ("heart_disease", "Heart disease"),
    ("cancer", "Cancer"),
    ("stroke", "Stroke"),
    ("asthma", "Asthma"),
)

_RELATIONS = (
    "mother",
    "father",
    "sister",
    "brother",
    "maternal grandmother",
    "paternal grandfather",
)

_CONCERN_TEMPLATES = (
    "Follow-up for {condition}",
    "{condition} - recheck and medication review",
    "Ongoing {condition}, wants to discuss treatment",
)

_GENERIC_CONCERNS = (
    "Sore throat and cough for about a week",
    "Annual check-up, no specific complaints",
    "Low back pain after lifting at work",
    "Headaches most afternoons for the past month",
)

_SPECIMENS = ("Serum", "Whole blood, EDTA", "Plasma, lithium heparin")

_BRACKET_BRAND = re.compile(r"\[([^\]]+)\]")
#: "NDA020800", "NDC0409" and friends: a registration number, not a drug name.
_REGISTRATION_CODE = re.compile(r"\b(?:NDA|ANDA|NDC|BLA)\d+\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]{2,}")
#: Units, dose forms and packaging words. Whatever is left after these is
#: the closest thing the product name has to a drug name.
_NOT_A_DRUG_WORD = frozenset(
    {
        "actuat",
        "auto-injector",
        "capsule",
        "capsules",
        "chewable",
        "cream",
        "day",
        "disintegrating",
        "dose",
        "drops",
        "extended",
        "film",
        "gel",
        "hour",
        "inert",
        "ingredients",
        "inhalation",
        "inhaler",
        "injection",
        "kit",
        "lotion",
        "mcg",
        "metered",
        "ointment",
        "ophthalmic",
        "oral",
        "pack",
        "patch",
        "pen",
        "prefilled",
        "release",
        "solution",
        "spray",
        "sublingual",
        "suppository",
        "suspension",
        "syringe",
        "tablet",
        "tablets",
        "topical",
        "transdermal",
        "unt",
        "vial",
    }
)


def display_name(raw: str) -> str:
    """
    Chart names verbatim.

    The local instance is seeded with Synthea data, whose names carry numeric
    suffixes ("Collier206"). They are kept exactly as the chart holds them: a
    document is supposed to agree with the chart, and quietly prettifying a
    name here would create a discrepancy nothing in the ground truth records.
    """
    return raw


def short_drug_name(product_name: str) -> str:
    """
    What a patient writes on a form, given a chart's full product name.

    Chart products are Synthea/RxNorm strings: a brand in brackets when there is
    one, otherwise a dose form, a strength and sometimes a leading registration
    number or a combination-pack expansion. A patient writes neither. The rule
    is: take the bracketed brand if present, otherwise the first one or two
    words that are actually drug names rather than units, dose forms or codes.

        "... Metered Dose Inhaler [Flovent]"                 -> "Flovent"
        "Loratadine 5 MG Chewable Tablet"                    -> "Loratadine"
        "NDA020800 0.3 ML Epinephrine 1 MG/ML Auto-Injector" -> "Epinephrine"
        "1 ML medroxyprogesterone acetate 150 MG/ML ..."     -> "medroxyprogesterone acetate"
    """
    brand = _BRACKET_BRAND.search(product_name)
    if brand:
        return brand.group(1).strip()

    # Drop registration numbers before tokenising: "NDA020800" would
    # otherwise leave a bare "NDA" behind as the first word.
    words = [
        w
        for w in _WORD_RE.findall(_REGISTRATION_CODE.sub(" ", product_name))
        if w.lower() not in _NOT_A_DRUG_WORD
    ]
    if not words:
        return product_name.strip(" {}[]/,")[:24]
    return " ".join(words[:2]).rstrip(",")


def condition_phrase(condition_name: str) -> str:
    """ "Childhood asthma (disorder)" -> "childhood asthma"."""
    return (
        re.sub(r"\s*\((disorder|finding|situation|procedure)\)\s*$", "", condition_name)
        .strip()
        .lower()
    )


# --------------------------------------------------------------------------
# Perturbation bookkeeping
# --------------------------------------------------------------------------


class PerturbationPlan:
    """
    Tracks the field-scoped perturbations so the renderers can ask about a
    field path, and so generate can fail loudly on a perturbation that named a
    field this document never renders.
    """

    def __init__(self, spec: ScenarioSpec) -> None:
        self.missing: dict[str, bool] = {
            str(p.get("field")): False
            for p in spec.perturbations_of(PerturbationKind.MISSING_FIELD)
        }
        self.illegible: dict[str, bool] = {
            str(p.get("field")): False
            for p in spec.perturbations_of(PerturbationKind.ILLEGIBLE_FIELD)
        }
        overlap = set(self.missing) & set(self.illegible)
        if overlap:
            raise SpecError(
                f"field(s) {sorted(overlap)} are both missing_field and illegible_field; pick one"
            )

    def is_missing(self, field_path: str) -> bool:
        if field_path in self.missing:
            self.missing[field_path] = True
            return True
        return False

    def is_illegible(self, field_path: str) -> bool:
        if field_path in self.illegible:
            self.illegible[field_path] = True
            return True
        return False

    def assert_all_applied(self, scenario_id: str, rendered_paths: Iterable[str]) -> None:
        known = sorted(set(rendered_paths))
        unapplied = sorted(
            [f for f, hit in self.missing.items() if not hit]
            + [f for f, hit in self.illegible.items() if not hit]
        )
        if unapplied:
            raise SpecError(
                f"{scenario_id}: perturbation field(s) {unapplied} are not rendered by this document. "
                f"Renderable field paths: {', '.join(known)}"
            )


# --------------------------------------------------------------------------
# Lab report content
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LabIdentity:
    name: str
    address: str
    city_state_zip: str
    phone: str
    clia: str
    director: str


@dataclass
class LabResult:
    panel_key: str
    test: LabTest
    value: float
    value_text: str
    flag: str
    interval_text: str
    forced_abnormal: bool = False

    @property
    def path(self) -> str:
        return f"results.{self.panel_key}.{self.test.key}"


@dataclass
class LabContent:
    lab: LabIdentity
    patient_name: str
    patient_name_is_wrong: bool
    chart_name: str
    dob: str
    sex: str
    mrn: str
    address_line: str
    city_state_zip: str
    accession: str
    specimen: str
    collected: dt.datetime
    received: dt.datetime
    reported: dt.datetime
    ordering_provider: str
    panels: list[tuple[LabPanel, list[LabResult]]]
    plan: PerturbationPlan
    perturbations: list[PerturbationRecord]


def plan_lab(spec: ScenarioSpec, chart: ChartRecord) -> LabContent:
    identity_rng = stream(spec.seed, "lab", "identity")
    order_rng = stream(spec.seed, "lab", "order")
    value_rng = stream(spec.seed, "lab", "values")

    lab = LabIdentity(*_LABS[identity_rng.randrange(len(_LABS))])
    plan = PerturbationPlan(spec)
    records: list[PerturbationRecord] = []

    chart_name = display_name(chart.full_name)
    patient_name = chart_name
    name_is_wrong = spec.has(PerturbationKind.WRONG_PATIENT_NAME)
    if name_is_wrong:
        patient_name = _WRONG_NAMES[stream(spec.seed, "wrong-name").randrange(len(_WRONG_NAMES))]
        records.append(
            PerturbationRecord(
                kind=PerturbationKind.WRONG_PATIENT_NAME.value,
                params={},
                field_paths=["patient.name"],
                chart_value=chart_name,
                document_value=patient_name,
                expected_discrepancy=(
                    f"The report is headed {patient_name!r} but the chart patient is {chart_name!r}; "
                    "the document does not belong to this chart."
                ),
            )
        )

    collected_day = ANCHOR_DATE - dt.timedelta(days=order_rng.randrange(2, 21))
    collected = dt.datetime.combine(
        collected_day, dt.time(order_rng.randrange(6, 11), order_rng.choice((5, 12, 24, 38, 47)))
    )
    received = collected + dt.timedelta(
        hours=order_rng.randrange(2, 7), minutes=order_rng.choice((3, 17, 29, 44))
    )
    reported = received + dt.timedelta(
        days=order_rng.randrange(0, 2), hours=order_rng.randrange(3, 14)
    )

    accession = (
        f"{collected_day.strftime('%y%m%d')}-"
        f"{order_rng.randrange(100, 1000)}-"
        f"{order_rng.randrange(1000, 10000)}"
    )

    multi_page = spec.has(PerturbationKind.MULTI_PAGE)
    panel_keys = MULTI_PAGE_PANELS if multi_page else SINGLE_PAGE_PANELS
    if multi_page:
        records.append(
            PerturbationRecord(
                kind=PerturbationKind.MULTI_PAGE.value,
                params={},
                field_paths=[],
                chart_value=None,
                document_value=f"{len(panel_keys)} panels",
                expected_discrepancy=(
                    "Layout perturbation, not a data discrepancy: the result table is long enough "
                    "to continue onto page 2, so an extractor must read past page 1."
                ),
            )
        )

    forced: dict[str, tuple[str, object]] = {}
    for perturbation in spec.perturbations_of(PerturbationKind.ABNORMAL_VALUE):
        target = str(perturbation.get("test"))
        resolved = find_test(target)
        if resolved is None:
            raise SpecError(f"{spec.scenario_id}: abnormal_value names unknown test {target!r}")
        panel, test = resolved
        if panel.key not in panel_keys:
            raise SpecError(
                f"{spec.scenario_id}: abnormal_value test {target!r} is in panel {panel.key!r}, which this "
                f"report does not order (panels: {', '.join(panel_keys)}). Add multi_page or pick another test."
            )
        forced[f"{panel.key}.{test.key}"] = (str(perturbation.get("direction")), perturbation)

    panels: list[tuple[LabPanel, list[LabResult]]] = []
    for panel_key in panel_keys:
        panel = PANELS_BY_KEY[panel_key]
        results: list[LabResult] = []
        for test in panel.tests:
            key = f"{panel.key}.{test.key}"
            direction_entry = forced.get(key)
            if direction_entry is not None:
                direction = direction_entry[0]
                forced_value = direction_entry[1].get("value")
                if forced_value is None:
                    value = test.draw_abnormal(value_rng, chart.sex, direction)
                else:
                    # An explicit value (e.g. a critical one for the H6 case).
                    # Must still sit on the requested side of the interval.
                    value = round(float(forced_value), test.decimals)
                    if test.flag(value, chart.sex) != ("H" if direction == "high" else "L"):
                        raise SpecError(
                            f"{spec.scenario_id}: abnormal_value {test.key!r} value {forced_value!r} "
                            f"is not {direction} of the reference interval"
                        )
            else:
                value = test.draw_normal(value_rng, chart.sex)
            result = LabResult(
                panel_key=panel.key,
                test=test,
                value=value,
                value_text=test.format_value(value),
                flag=test.flag(value, chart.sex),
                interval_text=test.format_interval(chart.sex),
                forced_abnormal=direction_entry is not None,
            )
            results.append(result)
            if direction_entry is not None:
                records.append(
                    PerturbationRecord(
                        kind=PerturbationKind.ABNORMAL_VALUE.value,
                        params=dict(direction_entry[1].params),
                        field_paths=[f"{result.path}.value", f"{result.path}.flag"],
                        chart_value=None,
                        document_value=f"{result.value_text} {test.unit}",
                        expected_discrepancy=(
                            f"{test.name} is reported as {result.value_text} {test.unit}, "
                            f"{'above' if result.flag == 'H' else 'below'} the reference interval "
                            f"{result.interval_text} {test.unit}, and flagged {result.flag!r}."
                        ),
                    )
                )
        panels.append((panel, results))

    return LabContent(
        lab=lab,
        patient_name=patient_name,
        patient_name_is_wrong=name_is_wrong,
        chart_name=chart_name,
        dob=chart.dob,
        sex=chart.sex,
        mrn=chart.mrn,
        address_line=chart.street,
        city_state_zip=chart.city_state_zip,
        accession=accession,
        specimen=_SPECIMENS[order_rng.randrange(len(_SPECIMENS))],
        collected=collected,
        received=received,
        reported=reported,
        ordering_provider=_ORDERING_PROVIDERS[order_rng.randrange(len(_ORDERING_PROVIDERS))],
        panels=panels,
        plan=plan,
        perturbations=records,
    )


# --------------------------------------------------------------------------
# Intake form content
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeClinic:
    name: str
    address: str
    phone: str


@dataclass
class IntakeMedication:
    #: What the patient wrote.
    written_name: str
    dose: str | None
    frequency: str
    #: The chart product name this line corresponds to, or None when the line
    #: is an addition the chart does not have.
    chart_name: str | None
    is_perturbed: bool = False
    note: str | None = None


@dataclass
class IntakeAllergy:
    allergen: str
    reaction: str
    chart_allergen: str | None
    is_perturbed: bool = False


@dataclass
class IntakeFamilyHistory:
    key: str
    label: str
    checked: bool
    relation: str | None


@dataclass
class IntakeContent:
    clinic: IntakeClinic
    patient_name: str
    patient_name_is_wrong: bool
    chart_name: str
    dob: str
    sex: str
    address_line: str
    city_state_zip: str
    phone: str
    visit_date: dt.date
    chief_concern: str
    chief_concern_from_chart: bool
    medications: list[IntakeMedication]
    allergies: list[IntakeAllergy]
    no_known_allergies: bool
    family_history: list[IntakeFamilyHistory]
    plan: PerturbationPlan
    perturbations: list[PerturbationRecord]


def plan_intake(spec: ScenarioSpec, chart: ChartRecord) -> IntakeContent:
    clinic_rng = stream(spec.seed, "intake", "clinic")
    visit_rng = stream(spec.seed, "intake", "visit")
    med_rng = stream(spec.seed, "intake", "medications")
    family_rng = stream(spec.seed, "intake", "family")

    clinic = IntakeClinic(*_CLINICS[clinic_rng.randrange(len(_CLINICS))])
    plan = PerturbationPlan(spec)
    records: list[PerturbationRecord] = []

    chart_name = display_name(chart.full_name)
    patient_name = chart_name
    name_is_wrong = spec.has(PerturbationKind.WRONG_PATIENT_NAME)
    if name_is_wrong:
        patient_name = _WRONG_NAMES[stream(spec.seed, "wrong-name").randrange(len(_WRONG_NAMES))]
        records.append(
            PerturbationRecord(
                kind=PerturbationKind.WRONG_PATIENT_NAME.value,
                params={},
                field_paths=["patient.name"],
                chart_value=chart_name,
                document_value=patient_name,
                expected_discrepancy=(
                    f"The form is filled out for {patient_name!r} but the chart patient is {chart_name!r}; "
                    "the document does not belong to this chart."
                ),
            )
        )

    visit_date = ANCHOR_DATE - dt.timedelta(days=visit_rng.randrange(0, 10))

    disorders = [c for c in chart.conditions if c.name.endswith("(disorder)")]
    if disorders:
        concern_source = disorders[-1]
        template = _CONCERN_TEMPLATES[visit_rng.randrange(len(_CONCERN_TEMPLATES))]
        chief_concern = template.format(condition=condition_phrase(concern_source.name))
        concern_from_chart = True
    else:
        chief_concern = _GENERIC_CONCERNS[visit_rng.randrange(len(_GENERIC_CONCERNS))]
        concern_from_chart = False

    # -- medications
    medications = [
        IntakeMedication(
            written_name=short_drug_name(med.name),
            dose=med.strength,
            frequency=_FREQUENCIES[med_rng.randrange(len(_FREQUENCIES))],
            chart_name=med.name,
        )
        for med in chart.medications
    ]

    for perturbation in spec.perturbations_of(PerturbationKind.MED_DISCREPANCY):
        mode = str(perturbation.get("mode"))
        if mode == "add":
            name, dose, frequency = _ADDED_MEDS[med_rng.randrange(len(_ADDED_MEDS))]
            index = med_rng.randrange(len(medications) + 1) if medications else 0
            medications.insert(
                index,
                IntakeMedication(
                    written_name=name,
                    dose=dose,
                    frequency=frequency,
                    chart_name=None,
                    is_perturbed=True,
                    note="not on the chart medication list",
                ),
            )
            records.append(
                PerturbationRecord(
                    kind=PerturbationKind.MED_DISCREPANCY.value,
                    params=dict(perturbation.params),
                    field_paths=[f"medications[{index}].name", f"medications[{index}].dose"],
                    chart_value=None,
                    document_value=f"{name} {dose}",
                    expected_discrepancy=(
                        f"The form lists {name} {dose}, which is not on the chart medication list."
                    ),
                )
            )
        elif mode == "omit":
            if not medications:
                raise SpecError(
                    f"{spec.scenario_id}: med_discrepancy omit, but the chart has no medications"
                )
            index = med_rng.randrange(len(medications))
            dropped = medications.pop(index)
            records.append(
                PerturbationRecord(
                    kind=PerturbationKind.MED_DISCREPANCY.value,
                    params=dict(perturbation.params),
                    field_paths=[],
                    chart_value=dropped.chart_name,
                    document_value=None,
                    expected_discrepancy=(
                        f"The chart lists {dropped.chart_name!r} as active; the form omits it entirely."
                    ),
                )
            )
        else:  # dose_change
            candidates = [i for i, m in enumerate(medications) if m.dose and m.chart_name]
            if not candidates:
                raise SpecError(
                    f"{spec.scenario_id}: med_discrepancy dose_change, but no chart medication has a "
                    "strength in its product name"
                )
            index = candidates[med_rng.randrange(len(candidates))]
            target = medications[index]
            original = target.dose or ""
            amount, _, unit = original.partition(" ")
            multiplier = med_rng.choice((0.5, 2.0, 4.0))
            try:
                changed_amount = float(amount) * multiplier
            except ValueError:  # pragma: no cover - strengths always parse as float
                changed_amount = multiplier
            changed = f"{changed_amount:g} {unit}".strip()
            medications[index] = IntakeMedication(
                written_name=target.written_name,
                dose=changed,
                frequency=target.frequency,
                chart_name=target.chart_name,
                is_perturbed=True,
                note=f"chart strength is {original}",
            )
            records.append(
                PerturbationRecord(
                    kind=PerturbationKind.MED_DISCREPANCY.value,
                    params=dict(perturbation.params),
                    field_paths=[f"medications[{index}].dose"],
                    chart_value=f"{target.chart_name} ({original})",
                    document_value=f"{target.written_name} {changed}",
                    expected_discrepancy=(
                        f"The form gives {target.written_name} as {changed}; the chart product is "
                        f"{target.chart_name!r}, i.e. {original}."
                    ),
                )
            )

    # -- allergies
    allergies = [
        IntakeAllergy(
            allergen=a.allergen,
            # Left blank when the chart has no resolvable reaction: writing
            # "not stated" would put text on the form that nothing backs.
            reaction=(a.reaction or ""),
            chart_allergen=a.allergen,
        )
        for a in chart.allergies
    ]

    for perturbation in spec.perturbations_of(PerturbationKind.ALLERGY_DISCREPANCY):
        mode = str(perturbation.get("mode"))
        if mode == "add":
            allergen, reaction = _ADDED_ALLERGIES[med_rng.randrange(len(_ADDED_ALLERGIES))]
            index = med_rng.randrange(len(allergies) + 1) if allergies else 0
            allergies.insert(index, IntakeAllergy(allergen, reaction, None, is_perturbed=True))
            records.append(
                PerturbationRecord(
                    kind=PerturbationKind.ALLERGY_DISCREPANCY.value,
                    params=dict(perturbation.params),
                    field_paths=[f"allergies[{index}].allergen", f"allergies[{index}].reaction"],
                    chart_value=None,
                    document_value=f"{allergen} ({reaction})",
                    expected_discrepancy=(
                        f"The form reports an allergy to {allergen} ({reaction}) that the chart does not carry."
                    ),
                )
            )
        else:  # omit
            if not allergies:
                raise SpecError(
                    f"{spec.scenario_id}: allergy_discrepancy omit, but the chart has no allergies"
                )
            index = med_rng.randrange(len(allergies))
            dropped = allergies.pop(index)
            records.append(
                PerturbationRecord(
                    kind=PerturbationKind.ALLERGY_DISCREPANCY.value,
                    params=dict(perturbation.params),
                    field_paths=[],
                    chart_value=dropped.chart_allergen,
                    document_value=None,
                    expected_discrepancy=(
                        f"The chart records an allergy to {dropped.chart_allergen!r}; the form omits it."
                    ),
                )
            )

    # -- family history (no chart counterpart; invented, and marked as such)
    family_history = []
    for key, label in _FAMILY_HISTORY_CONDITIONS:
        checked = family_rng.random() < 0.38
        relation = _RELATIONS[family_rng.randrange(len(_RELATIONS))] if checked else None
        family_history.append(
            IntakeFamilyHistory(key=key, label=label, checked=checked, relation=relation)
        )

    return IntakeContent(
        clinic=clinic,
        patient_name=patient_name,
        patient_name_is_wrong=name_is_wrong,
        chart_name=chart_name,
        dob=chart.dob,
        sex=chart.sex,
        address_line=chart.street,
        city_state_zip=chart.city_state_zip,
        phone=chart.phone,
        visit_date=visit_date,
        chief_concern=chief_concern,
        chief_concern_from_chart=concern_from_chart,
        medications=medications,
        allergies=allergies,
        no_known_allergies=not allergies,
        family_history=family_history,
        plan=plan,
        perturbations=records,
    )
