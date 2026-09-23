"""
Printed test name (+ unit) -> LOINC code. A committed table, nothing clever.

`LabResult.loinc_code` is assigned, never read off the page (lab_pdf.py),
and the write-back planner refuses an uncoded row (`no_loinc_code`), so
without this step no extracted lab value is ever writable. This is that
step, kept deliberately small:

* **A committed table.** `LOINC_TABLE` lists the analytes the doc-factory
  draws, each with the unit it is reported in and the LOINC code for that
  measurement. Adding a test is adding a row, reviewed like any other code.
* **Exact match on the normalised printed name.** Case, punctuation and
  spacing are folded (`Protein, Total` == `protein total`); nothing else
  is. No synonyms, no stemming, no fuzzy matching, no terminology service:
  a near match on a test name is a guess at which test was run, and the
  code decides what the chart says was measured.
* **Unit as the tiebreak, and as a veto.** Where one name has several
  entries (glucose in mg/dL is 2345-7; in mmol/L it is 14749-6) the unit
  chooses. Where the model read a unit that matches no entry for the name,
  the row is unmapped even when the name is unique: a glucose reported in
  mmol/L must never be written under the mg/dL code, because the number
  would then be read as a different quantity. Only when no unit was read at
  all does a name with a single entry map on its own.
* **Only verified text drives it.** The name must be a located fact; an
  unlocated name maps to nothing (and the planner would refuse the row for
  `no_test_name` anyway).

Anything unmapped comes back with a reason and stays uncoded, so the
planner skips it as `no_loinc_code` — cleanly, as before this table existed.

Codes are LOINC's serum/plasma (or blood, for the CBC and A1c) codes for
each analyte in the unit shown. `DELIBERATELY_UNMAPPED` lists what the
factory draws that this table refuses to code, and why.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from app.extraction.normalize import normalize
from app.models.w2.extraction import ExtractedField, FieldStatus


@dataclass(frozen=True)
class LoincEntry:
    name: str
    unit: str
    code: str
    #: LOINC's long common name, abbreviated; for the reviewer, not the code.
    meaning: str


LOINC_TABLE: tuple[LoincEntry, ...] = (
    # -- comprehensive metabolic panel -------------------------------------
    LoincEntry("Glucose", "mg/dL", "2345-7", "Glucose [Mass/volume] in Serum or Plasma"),
    LoincEntry("Glucose", "mmol/L", "14749-6", "Glucose [Moles/volume] in Serum or Plasma"),
    LoincEntry("BUN (Urea Nitrogen)", "mg/dL", "3094-0", "Urea nitrogen [Mass/volume] in S/P"),
    LoincEntry("Creatinine", "mg/dL", "2160-0", "Creatinine [Mass/volume] in Serum or Plasma"),
    LoincEntry("Sodium", "mmol/L", "2951-2", "Sodium [Moles/volume] in Serum or Plasma"),
    LoincEntry("Potassium", "mmol/L", "2823-3", "Potassium [Moles/volume] in Serum or Plasma"),
    LoincEntry("Chloride", "mmol/L", "2075-0", "Chloride [Moles/volume] in Serum or Plasma"),
    LoincEntry(
        "CO2 (Bicarbonate)", "mmol/L", "2028-9", "Carbon dioxide, total [Moles/volume] in S/P"
    ),
    LoincEntry("Calcium", "mg/dL", "17861-6", "Calcium [Mass/volume] in Serum or Plasma"),
    LoincEntry("Protein, Total", "g/dL", "2885-2", "Protein [Mass/volume] in Serum or Plasma"),
    LoincEntry("Albumin", "g/dL", "1751-7", "Albumin [Mass/volume] in Serum or Plasma"),
    LoincEntry("Bilirubin, Total", "mg/dL", "1975-2", "Bilirubin.total [Mass/volume] in S/P"),
    LoincEntry(
        "Alkaline Phosphatase",
        "U/L",
        "6768-6",
        "Alkaline phosphatase [Enzymatic activity/volume] in S/P",
    ),
    LoincEntry(
        "AST (SGOT)",
        "U/L",
        "1920-8",
        "Aspartate aminotransferase [Enzymatic activity/volume] in S/P",
    ),
    LoincEntry(
        "ALT (SGPT)", "U/L", "1742-6", "Alanine aminotransferase [Enzymatic activity/volume] in S/P"
    ),
    # -- lipid panel ---------------------------------------------------------
    LoincEntry("Cholesterol, Total", "mg/dL", "2093-3", "Cholesterol [Mass/volume] in S/P"),
    LoincEntry("Triglycerides", "mg/dL", "2571-8", "Triglyceride [Mass/volume] in S/P"),
    LoincEntry("HDL Cholesterol", "mg/dL", "2085-9", "Cholesterol in HDL [Mass/volume] in S/P"),
    LoincEntry(
        "LDL Cholesterol (calc)",
        "mg/dL",
        "13457-7",
        "Cholesterol in LDL [Mass/volume] in S/P by calculation",
    ),
    LoincEntry(
        "Total Chol / HDL Ratio",
        "ratio",
        "9830-1",
        "Cholesterol.total/Cholesterol in HDL [Mass Ratio] in S/P",
    ),
    # -- A1c -----------------------------------------------------------------
    LoincEntry("Hemoglobin A1c", "%", "4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood"),
    LoincEntry(
        "Hemoglobin A1c",
        "mmol/mol",
        "59261-8",
        "Hemoglobin A1c/Hemoglobin.total in Blood by IFCC protocol",
    ),
    LoincEntry(
        "Est. Average Glucose",
        "mg/dL",
        "27353-2",
        "Glucose mean value [Mass/volume] in Blood estimated from HbA1c",
    ),
    # -- complete blood count ------------------------------------------------
    LoincEntry("WBC", "K/uL", "6690-2", "Leukocytes [#/volume] in Blood by Automated count"),
    LoincEntry("RBC", "M/uL", "789-8", "Erythrocytes [#/volume] in Blood by Automated count"),
    LoincEntry("Hemoglobin", "g/dL", "718-7", "Hemoglobin [Mass/volume] in Blood"),
    LoincEntry(
        "Hematocrit", "%", "4544-3", "Hematocrit [Volume Fraction] of Blood by Automated count"
    ),
    LoincEntry("MCV", "fL", "787-2", "MCV [Entitic volume] by Automated count"),
    LoincEntry("MCH", "pg", "785-6", "MCH [Entitic mass] by Automated count"),
    LoincEntry("MCHC", "g/dL", "786-4", "MCHC [Mass/volume] by Automated count"),
    LoincEntry("RDW", "%", "788-0", "Erythrocyte distribution width [Ratio] by Automated count"),
    LoincEntry("Platelets", "K/uL", "777-3", "Platelets [#/volume] in Blood by Automated count"),
    LoincEntry(
        "Neutrophils", "%", "770-8", "Neutrophils/100 leukocytes in Blood by Automated count"
    ),
    LoincEntry(
        "Lymphocytes", "%", "736-9", "Lymphocytes/100 leukocytes in Blood by Automated count"
    ),
    LoincEntry("Monocytes", "%", "5905-5", "Monocytes/100 leukocytes in Blood by Automated count"),
    # -- thyroid -------------------------------------------------------------
    LoincEntry("TSH", "uIU/mL", "3016-3", "Thyrotropin [Units/volume] in Serum or Plasma"),
    LoincEntry("Free T4", "ng/dL", "3024-7", "Thyroxine (T4) free [Mass/volume] in S/P"),
    LoincEntry("T3, Total", "ng/dL", "3053-6", "Triiodothyronine (T3) [Mass/volume] in S/P"),
    # -- iron studies and vitamins -------------------------------------------
    LoincEntry("Iron, Total", "ug/dL", "2498-4", "Iron [Mass/volume] in Serum or Plasma"),
    LoincEntry(
        "Iron Binding Capacity", "ug/dL", "2500-7", "Iron binding capacity [Mass/volume] in S/P"
    ),
    LoincEntry("Iron Saturation", "%", "2502-3", "Iron saturation [Mass Fraction] in S/P"),
    LoincEntry("Ferritin", "ng/mL", "2276-4", "Ferritin [Mass/volume] in Serum or Plasma"),
    LoincEntry(
        "Vitamin D, 25-Hydroxy", "ng/mL", "62292-8", "25-Hydroxyvitamin D2+D3 [Mass/volume] in S/P"
    ),
    LoincEntry("Vitamin B-12", "pg/mL", "2132-9", "Cobalamin (Vitamin B12) [Mass/volume] in S/P"),
    LoincEntry("Folate, Serum", "ng/mL", "2284-8", "Folate [Mass/volume] in Serum or Plasma"),
)

#: What the factory draws that this table will not code, and why.
DELIBERATELY_UNMAPPED: dict[str, str] = {
    "eGFR": (
        "LOINC codes an estimated GFR by the equation that produced it (MDRD, "
        "CKD-EPI 2009, CKD-EPI 2021 race-free, ...), each with its own code. "
        "The report prints only 'eGFR', so choosing one would assert a method "
        "the page does not state."
    ),
}


class LoincOutcome(StrEnum):
    MAPPED = "mapped"
    #: The test name is not a located fact.
    NO_NAME = "no_name"
    #: The normalised name is not in the table.
    NAME_NOT_IN_TABLE = "name_not_in_table"
    #: The name is in the table, but the unit the model read matches no entry for it.
    UNIT_MISMATCH = "unit_mismatch"
    #: Several entries share the name and no unit was read to choose between them.
    AMBIGUOUS_WITHOUT_UNIT = "ambiguous_without_unit"


@dataclass(frozen=True)
class LoincAssignment:
    code: str | None
    outcome: LoincOutcome


_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalise_test_name(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(_NON_ALNUM.sub(" ", folded).split())


def normalise_unit(text: str) -> str:
    return normalize(text).replace(" ", "")


_BY_NAME: dict[str, list[LoincEntry]] = {}
for _entry in LOINC_TABLE:
    _BY_NAME.setdefault(normalise_test_name(_entry.name), []).append(_entry)


def assign_loinc(test_name: ExtractedField[str], unit: ExtractedField[str]) -> LoincAssignment:
    """The code for one row, or why there is none. See the module docstring."""
    name = test_name.as_presented_fact()
    if name is None:
        return LoincAssignment(None, LoincOutcome.NO_NAME)
    candidates = _BY_NAME.get(normalise_test_name(name.value), [])
    if not candidates:
        return LoincAssignment(None, LoincOutcome.NAME_NOT_IN_TABLE)

    # Any unit the model read counts, located or not: a unit it read that
    # disagrees with the table is a veto even if the page could not confirm
    # it, because a wrong code is worse than none.
    read_unit = unit.value if unit.status in (FieldStatus.LOCATED, FieldStatus.UNLOCATED) else None
    if read_unit is not None and read_unit.strip():
        wanted = normalise_unit(read_unit)
        matching = [entry for entry in candidates if normalise_unit(entry.unit) == wanted]
        if len(matching) == 1:
            return LoincAssignment(matching[0].code, LoincOutcome.MAPPED)
        return LoincAssignment(None, LoincOutcome.UNIT_MISMATCH)

    if len(candidates) == 1:
        return LoincAssignment(candidates[0].code, LoincOutcome.MAPPED)
    return LoincAssignment(None, LoincOutcome.AMBIGUOUS_WITHOUT_UNIT)
