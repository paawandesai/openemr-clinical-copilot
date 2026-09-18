"""
LOINC names and the question-wording-to-LOINC rules for lab follow-ups.

Why this exists. app/models/fhir_resources.py:Observation carries a LOINC
code and deliberately no name (ARCHITECTURE.md §5: "lab claims bind to
source_id and a LOINC code"), and this build may not change that model or
its reader. A physician does not read LOINC codes, so a lab line needs a
name from somewhere that is neither the model nor a guess:

- LOINC_NAMES is code-authored wording, like the record and field labels
  in app/graph/follow_up.py. Every entry but the two marked below is the
  `code.coding.display` OpenEMR's own FHIR API returned for that code on
  the deployment host (read 2026-09-18, all 52 patients, every laboratory
  Observation with a value): LOINC's long common name, verbatim. The
  renderer looks a name up by the loinc_code of the *retrieved record*,
  never by anything the model wrote. A code not in the table renders as
  its code, flagged as unnamed; it is never given a guessed name.
- ANALYTE_RULES maps words in a question to LOINC codes so that a chart
  with 2,000 lab results (the largest on the host has 2,203) can be
  narrowed by rule to the handful a question is about before the model
  sees any of them (app/conversation/routing.py:narrow_observations).

Known limitation: the proper fix is a name on the Observation model, read
from the chart record and verified like any other field. This table only
covers codes seen on the host plus hemoglobin A1c; anything else is shown
by code alone.
"""

from __future__ import annotations

import re

LOINC_NAMES: dict[str, str] = {
    "10230-1": "Left ventricular Ejection fraction",
    "10834-0": "Globulin [Mass/volume] in Serum by calculation",
    "13945-1": "Erythrocytes [#/area] in Urine sediment by Microscopy high power field",
    "14627-4": "Bicarbonate [Moles/volume] in Venous blood",
    "1742-6": "Alanine aminotransferase [Enzymatic activity/volume] in Serum or Plasma",
    "1751-7": "Albumin [Mass/volume] in Serum or Plasma",
    "17861-6": "Calcium [Mass/volume] in Serum or Plasma",
    "18262-6": "Cholesterol in LDL [Mass/volume] in Serum or Plasma by Direct assay",
    "1920-8": "Aspartate aminotransferase [Enzymatic activity/volume] in Serum or Plasma",
    "1960-4": "Bicarbonate [Moles/volume] in Arterial blood",
    "1975-2": "Bilirubin.total [Mass/volume] in Serum or Plasma",
    "2019-8": "Carbon dioxide [Partial pressure] in Arterial blood",
    "2021-4": "Carbon dioxide [Partial pressure] in Venous blood",
    "2027-1": "Carbon dioxide, total [Moles/volume] in Venous blood",
    "2028-9": "Carbon dioxide, total [Moles/volume] in Serum or Plasma",
    "20454-5": "Protein [Presence] in Urine by Test strip",
    "20505-4": "Bilirubin.total [Mass/volume] in Urine by Test strip",
    "20565-8": "Carbon dioxide, total [Moles/volume] in Blood",
    "20570-8": "Hematocrit [Volume Fraction] of Blood by calculation",
    "2069-3": "Chloride [Moles/volume] in Blood",
    "2075-0": "Chloride [Moles/volume] in Serum or Plasma",
    "2085-9": "Cholesterol in HDL [Mass/volume] in Serum or Plasma",
    "2093-3": "Cholesterol [Mass/volume] in Serum or Plasma",
    "2160-0": "Creatinine [Mass/volume] in Serum or Plasma",
    "2339-0": "Glucose [Mass/volume] in Blood",
    "2345-7": "Glucose [Mass/volume] in Serum or Plasma",
    "2514-8": "Ketones [Presence] in Urine by Test strip",
    "25428-4": "Glucose [Presence] in Urine by Test strip",
    "2571-8": "Triglyceride [Mass/volume] in Serum or Plasma",
    "2703-7": "Oxygen [Partial pressure] in Arterial blood",
    "2705-2": "Oxygen [Partial pressure] in Venous blood",
    "2708-6": "Oxygen saturation in Arterial blood",
    "2744-1": "pH of Arterial blood",
    "2746-6": "pH of Venous blood",
    "2823-3": "Potassium [Moles/volume] in Serum or Plasma",
    "2885-2": "Protein [Mass/volume] in Serum or Plasma",
    "2947-0": "Sodium [Moles/volume] in Blood",
    "2951-2": "Sodium [Moles/volume] in Serum or Plasma",
    "3094-0": "Urea nitrogen [Mass/volume] in Serum or Plasma",
    "32207-3": "Platelet distribution width [Entitic volume] in Blood by Automated count",
    "32623-1": "Platelet [Entitic mean volume] in Blood by Automated count",
    "33914-3": (
        "Glomerular filtration rate [Volume Rate/Area] in Serum or Plasma by "
        "Creatinine-based formula (MDRD)/1.73 sq M"
    ),
    "38483-4": "Creatinine [Mass/volume] in Blood",
    "44261-6": "Patient Health Questionnaire 9 item (PHQ-9) total score [Reported]",
    "4544-3": "Hematocrit [Volume Fraction] of Blood by Automated count",
    "49765-1": "Calcium [Mass/volume] in Blood",
    "55758-7": "Patient Health Questionnaire 2 item (PHQ-2) total score [Reported]",
    "5770-3": "Bilirubin.total [Presence] in Urine by Test strip",
    "5787-7": "Epithelial cells [#/area] in Urine sediment by Microscopy high power field",
    "5792-7": "Glucose [Mass/volume] in Urine by Test strip",
    "5797-6": "Ketones [Mass/volume] in Urine by Test strip",
    "5802-4": "Nitrite [Presence] in Urine by Test strip",
    "5803-2": "pH of Urine by Test strip",
    "5804-0": "Protein [Mass/volume] in Urine by Test strip",
    "5811-5": "Specific gravity of Urine by Test strip",
    "5821-4": "Leukocytes [#/area] in Urine sediment by Microscopy high power field",
    "5902-2": "Prothrombin time (PT)",
    "59460-6": "Fall risk total [Morse Fall Scale]",
    "6298-4": "Potassium [Moles/volume] in Blood",
    "6299-2": "Urea nitrogen [Mass/volume] in Blood",
    "6301-6": "INR in Platelet poor plasma by Coagulation assay",
    "6690-2": "Leukocytes [#/volume] in Blood by Automated count",
    "6768-6": "Alkaline phosphatase [Enzymatic activity/volume] in Serum or Plasma",
    "70274-6": "Generalized anxiety disorder 7 item (GAD-7) total score [Reported.PHQ]",
    "718-7": "Hemoglobin [Mass/volume] in Blood",
    "75626-2": "Total score [AUDIT-C]",
    "76504-0": "Total score [HARK]",
    "777-3": "Platelets [#/volume] in Blood by Automated count",
    "785-6": "MCH [Entitic mass] by Automated count",
    "786-4": "MCHC [Entitic Mass/volume] in Red Blood Cells by Automated count",
    "787-2": "MCV [Entitic mean volume] in Red Blood Cells by Automated count",
    "788-0": "Erythrocyte [DistWidth] in Blood by Automated count",
    "789-8": "Erythrocytes [#/volume] in Blood by Automated count",
    "82667-7": "Total score [DAST-10]",
    "89204-2": (
        "Patient Health Questionnaire-9: Modified for Teens total score "
        "[Reported.PHQ.Teen]"
    ),
    "89579-7": "Troponin I.cardiac [Mass/volume] in Serum or Plasma by High sensitivity method",
    # Not seen on the host (no patient there has one); LOINC's own long
    # common names. Present so the most common lab follow-up of all is
    # narrowed to its codes and honestly answered "no such result".
    "4548-4": "Hemoglobin A1c/Hemoglobin.total in Blood",
    "17856-6": "Hemoglobin A1c/Hemoglobin.total in Blood by HPLC",
}

_BMP = (
    "2339-0", "2345-7", "6299-2", "3094-0", "38483-4", "2160-0", "49765-1", "17861-6",
    "2947-0", "2951-2", "6298-4", "2823-3", "2069-3", "2075-0", "20565-8", "2028-9",
)
_KIDNEY = ("33914-3", "38483-4", "2160-0", "6299-2", "3094-0")
_LIPIDS = ("2093-3", "18262-6", "2085-9", "2571-8")
_CBC = (
    "6690-2", "789-8", "718-7", "4544-3", "20570-8", "787-2", "785-6", "786-4", "788-0",
    "777-3", "32207-3", "32623-1",
)
_LIVER = ("1742-6", "1920-8", "6768-6", "1975-2", "1751-7", "2885-2", "10834-0")
_URINALYSIS = (
    "2514-8", "5811-5", "5803-2", "5802-4", "5792-7", "5804-0", "25428-4",
    "5770-3", "20454-5", "20505-4", "5797-6", "13945-1", "5787-7", "5821-4",
)

# (rule id, wording, codes). Rule ids are logged; the wording never is.
# Order matters only for HEMOGLOBIN_A1C, which claims "hemoglobin a1c"
# before HEMOGLOBIN can.
ANALYTE_RULES: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (rule_id, re.compile(pattern, re.IGNORECASE), codes)
    for rule_id, pattern, codes in (
        ("hemoglobin_a1c", r"\b(hb\s?a1c|a1c|glycated|glycosylated|h(a?e)moglobin a1c)\b", ("4548-4", "17856-6")),
        ("glucose", r"\b(glucose|blood sugars?|sugars?)\b", ("2339-0", "2345-7")),
        ("creatinine", r"\bcreatinine\b", ("38483-4", "2160-0")),
        ("kidney_function", r"\b(e?gfr|glomerular|(kidney|renal) (function|labs?|panel))\b", _KIDNEY),
        ("urea_nitrogen", r"\b(bun|urea)\b", ("6299-2", "3094-0")),
        ("sodium", r"\bsodium\b", ("2947-0", "2951-2")),
        ("potassium", r"\bpotassium\b", ("6298-4", "2823-3")),
        ("chloride", r"\bchloride\b", ("2069-3", "2075-0")),
        ("calcium", r"\bcalcium\b", ("49765-1", "17861-6")),
        ("carbon_dioxide", r"\b(co2|bicarb(onate)?|carbon dioxide)\b", ("20565-8", "2028-9", "2027-1", "14627-4", "1960-4")),
        ("metabolic_panel", r"\b(electrolytes?|bmp|cmp|(basic|comprehensive) metabolic|metabolic panel|chem(istry)?\s?(7|panel)?)\b", _BMP),
        ("lipids", r"\b(lipids?|lipid panel|cholesterol)\b", _LIPIDS),
        ("ldl", r"\bldl\b", ("18262-6",)),
        ("hdl", r"\bhdl\b", ("2085-9",)),
        ("triglycerides", r"\btriglycerides?\b", ("2571-8",)),
        ("hemoglobin", r"\b(h(a?e)moglobin|hgb)\b(?!\s*a1c)", ("718-7",)),
        ("hematocrit", r"\b(hematocrit|hct)\b", ("4544-3", "20570-8")),
        ("white_cells", r"\b(wbc|white (blood )?cells?|leukocytes?)\b", ("6690-2",)),
        ("red_cells", r"\b(rbc|red (blood )?cells?|erythrocytes?)\b", ("789-8",)),
        ("platelets", r"\b(platelets?|plt)\b", ("777-3",)),
        ("blood_count", r"\b(cbc|(complete )?blood count)\b", _CBC),
        ("alt", r"\b(alt|alanine aminotransferase|sgpt)\b", ("1742-6",)),
        ("ast", r"\b(ast|aspartate aminotransferase|sgot)\b", ("1920-8",)),
        ("alkaline_phosphatase", r"\b(alk(aline)? phos(phatase)?|alp)\b", ("6768-6",)),
        ("bilirubin", r"\bbilirubin\b", ("1975-2",)),
        ("albumin", r"\balbumin\b", ("1751-7",)),
        ("liver_panel", r"\b(lfts?|(liver|hepatic) (function|panel|enzymes?|labs?))\b", _LIVER),
        ("inr", r"\b(inr|prothrombin|pro\s?time)\b", ("6301-6", "5902-2")),
        ("troponin", r"\btroponins?\b", ("89579-7",)),
        ("ejection_fraction", r"\b(ejection fraction|lvef)\b", ("10230-1",)),
        ("urinalysis", r"\b(urinalysis|urine (dip(stick)?|test|results?|labs?))\b", _URINALYSIS),
        ("phq", r"\bphq(-|\s)?(2|9)?\b", ("55758-7", "44261-6", "89204-2")),
        ("gad7", r"\bgad(-|\s)?7?\b", ("70274-6",)),
        ("audit_c", r"\baudit(-|\s)?c\b", ("75626-2",)),
        ("dast", r"\bdast(-|\s)?(10)?\b", ("82667-7",)),
        ("hark", r"\bhark\b", ("76504-0",)),
        ("fall_risk", r"\b(fall risk|morse)\b", ("59460-6",)),
    )
)


# LOINC codes that are the same analyte measured in a different specimen or
# by a different method (same LOINC component and property). "Her most
# recent creatinine" means the latest across both, so the renderer ranks a
# result's recency within its set, not within its one code: on the host, one
# patient's latest serum creatinine is a month older than her latest blood
# creatinine, and calling the former "most recent" would be true of the code
# and wrong for the physician. Only ever makes the recency tag more
# cautious. Panels (BMP, CBC) are deliberately not sets: their members are
# different tests.
SAME_TEST_CODES: tuple[frozenset[str], ...] = tuple(
    frozenset(codes)
    for codes in (
        ("4548-4", "17856-6"),
        ("2339-0", "2345-7"),
        ("38483-4", "2160-0"),
        ("6299-2", "3094-0"),
        ("49765-1", "17861-6"),
        ("2947-0", "2951-2"),
        ("6298-4", "2823-3"),
        ("2069-3", "2075-0"),
        ("20565-8", "2028-9", "2027-1"),
        ("14627-4", "1960-4"),
        ("4544-3", "20570-8"),
    )
)


def same_test_codes(code: str) -> frozenset[str]:
    """The codes recency is ranked across for `code`: its set, or itself alone."""
    for codes in SAME_TEST_CODES:
        if code in codes:
            return codes
    return frozenset({code})


def loinc_name(code: str) -> str | None:
    return LOINC_NAMES.get(code)


def analyte_matches(question: str) -> list[tuple[str, tuple[str, ...]]]:
    """Every analyte rule the question's wording fires, in table order."""
    return [
        (rule_id, codes)
        for rule_id, pattern, codes in ANALYTE_RULES
        if pattern.search(question)
    ]
