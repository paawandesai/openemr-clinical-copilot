"""
Lab panel catalog.

Reference intervals are the ordinary adult intervals a US outpatient lab prints
on a report. They exist here to make the generated documents look and read like
real reports and to give the H/L flag something to be right or wrong about —
they are not a clinical reference.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class LabTest:
    key: str
    name: str
    unit: str
    low: float
    high: float
    decimals: int
    #: Female-specific interval where the usual adult interval is sex-split.
    low_female: float | None = None
    high_female: float | None = None

    def interval(self, sex: str) -> tuple[float, float]:
        if sex.strip().lower().startswith("f") and self.low_female is not None and self.high_female is not None:
            return self.low_female, self.high_female
        return self.low, self.high

    def format_value(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"

    def format_interval(self, sex: str) -> str:
        low, high = self.interval(sex)
        return f"{self.format_value(low)} - {self.format_value(high)}"

    def flag(self, value: float, sex: str) -> str:
        low, high = self.interval(sex)
        if value < low:
            return "L"
        if value > high:
            return "H"
        return ""

    def draw_normal(self, rng: random.Random, sex: str) -> float:
        """A value comfortably inside the interval, clear of both bounds."""
        low, high = self.interval(sex)
        span = high - low
        centre = (low + high) / 2
        value = rng.gauss(centre, span / 5.5)
        value = min(max(value, low + span * 0.06), high - span * 0.06)
        return round(value, self.decimals)

    def draw_abnormal(self, rng: random.Random, sex: str, direction: str) -> float:
        """A value unambiguously outside the interval on the requested side."""
        low, high = self.interval(sex)
        span = max(high - low, 10 ** -self.decimals)
        step = 10 ** -self.decimals
        if direction == "high":
            return round(high + span * rng.uniform(0.18, 0.55) + step, self.decimals)

        # Going low is capped at a fraction of the lower bound, not of the
        # interval width: a wide interval (ferritin 15 - 150) would otherwise
        # push the result to zero, which no analyser reports.
        drop = span * rng.uniform(0.18, 0.55)
        if low > 0:
            drop = min(drop, low * 0.6)
        value = round(max(low - drop, step), self.decimals)
        if value >= low:  # rounding landed back on the bound
            value = round(max(low - step, step), self.decimals)
        return value


@dataclass(frozen=True)
class LabPanel:
    key: str
    name: str
    #: What the report prints as the ordered test, e.g. "80053".
    order_code: str
    tests: tuple[LabTest, ...]


CMP = LabPanel(
    key="cmp",
    name="Comprehensive Metabolic Panel",
    order_code="80053",
    tests=(
        LabTest("glucose", "Glucose", "mg/dL", 70, 99, 0),
        LabTest("bun", "BUN (Urea Nitrogen)", "mg/dL", 7, 20, 0),
        LabTest("creatinine", "Creatinine", "mg/dL", 0.60, 1.30, 2, 0.50, 1.10),
        LabTest("egfr", "eGFR", "mL/min/1.73", 60, 120, 0),
        LabTest("sodium", "Sodium", "mmol/L", 136, 145, 0),
        LabTest("potassium", "Potassium", "mmol/L", 3.5, 5.1, 1),
        LabTest("chloride", "Chloride", "mmol/L", 98, 107, 0),
        LabTest("co2", "CO2 (Bicarbonate)", "mmol/L", 22, 29, 0),
        LabTest("calcium", "Calcium", "mg/dL", 8.6, 10.2, 1),
        LabTest("protein_total", "Protein, Total", "g/dL", 6.4, 8.3, 1),
        LabTest("albumin", "Albumin", "g/dL", 3.5, 5.0, 1),
        LabTest("bilirubin_total", "Bilirubin, Total", "mg/dL", 0.2, 1.2, 1),
        LabTest("alp", "Alkaline Phosphatase", "U/L", 40, 129, 0),
        LabTest("ast", "AST (SGOT)", "U/L", 10, 40, 0),
        LabTest("alt", "ALT (SGPT)", "U/L", 7, 56, 0),
    ),
)

LIPID = LabPanel(
    key="lipid",
    name="Lipid Panel",
    order_code="80061",
    tests=(
        LabTest("cholesterol_total", "Cholesterol, Total", "mg/dL", 125, 200, 0),
        LabTest("triglycerides", "Triglycerides", "mg/dL", 35, 150, 0),
        LabTest("hdl", "HDL Cholesterol", "mg/dL", 40, 100, 0, 50, 100),
        LabTest("ldl_calc", "LDL Cholesterol (calc)", "mg/dL", 40, 100, 0),
        LabTest("chol_hdl_ratio", "Total Chol / HDL Ratio", "ratio", 1.5, 5.0, 1),
    ),
)

CBC = LabPanel(
    key="cbc",
    name="CBC with Differential",
    order_code="85025",
    tests=(
        LabTest("wbc", "WBC", "K/uL", 4.0, 11.0, 1),
        LabTest("rbc", "RBC", "M/uL", 4.20, 5.80, 2, 3.80, 5.20),
        LabTest("hemoglobin", "Hemoglobin", "g/dL", 13.5, 17.5, 1, 12.0, 15.5),
        LabTest("hematocrit", "Hematocrit", "%", 41.0, 53.0, 1, 36.0, 46.0),
        LabTest("mcv", "MCV", "fL", 80, 100, 0),
        LabTest("mch", "MCH", "pg", 27.0, 33.0, 1),
        LabTest("mchc", "MCHC", "g/dL", 32.0, 36.0, 1),
        LabTest("rdw", "RDW", "%", 11.5, 14.5, 1),
        LabTest("platelets", "Platelets", "K/uL", 150, 400, 0),
        LabTest("neutrophils_pct", "Neutrophils", "%", 40, 70, 0),
        LabTest("lymphocytes_pct", "Lymphocytes", "%", 20, 45, 0),
        LabTest("monocytes_pct", "Monocytes", "%", 2, 12, 0),
    ),
)

A1C = LabPanel(
    key="a1c",
    name="Hemoglobin A1c",
    order_code="83036",
    tests=(
        LabTest("hba1c", "Hemoglobin A1c", "%", 4.0, 5.6, 1),
        LabTest("eag", "Est. Average Glucose", "mg/dL", 68, 114, 0),
    ),
)

THYROID = LabPanel(
    key="thyroid",
    name="Thyroid Panel",
    order_code="84443",
    tests=(
        LabTest("tsh", "TSH", "uIU/mL", 0.450, 4.500, 3),
        LabTest("t4_free", "Free T4", "ng/dL", 0.82, 1.77, 2),
        LabTest("t3_total", "T3, Total", "ng/dL", 71, 180, 0),
    ),
)

IRON_VITAMINS = LabPanel(
    key="iron_vitamins",
    name="Iron and Vitamin Studies",
    order_code="83540",
    tests=(
        LabTest("iron", "Iron, Total", "ug/dL", 50, 180, 0),
        LabTest("tibc", "Iron Binding Capacity", "ug/dL", 250, 450, 0),
        LabTest("iron_saturation", "Iron Saturation", "%", 15, 55, 0),
        LabTest("ferritin", "Ferritin", "ng/mL", 30, 400, 0, 15, 150),
        LabTest("vitamin_d", "Vitamin D, 25-Hydroxy", "ng/mL", 30.0, 100.0, 1),
        LabTest("vitamin_b12", "Vitamin B-12", "pg/mL", 232, 1245, 0),
        LabTest("folate", "Folate, Serum", "ng/mL", 3.0, 20.0, 1),
    ),
)

#: Ordered so that a scenario's panel selection is stable across runs.
PANELS: tuple[LabPanel, ...] = (CMP, CBC, LIPID, A1C, THYROID, IRON_VITAMINS)
PANELS_BY_KEY: dict[str, LabPanel] = {p.key: p for p in PANELS}

#: Panels a single-page report draws from, in order.
SINGLE_PAGE_PANELS: tuple[str, ...] = ("cmp", "lipid", "a1c")
#: Panels a multi-page report draws from — deliberately more than one page holds.
MULTI_PAGE_PANELS: tuple[str, ...] = ("cmp", "cbc", "lipid", "a1c", "thyroid", "iron_vitamins")


def find_test(name: str) -> tuple[LabPanel, LabTest] | None:
    """Resolve a perturbation's ``test:`` — by test key, or ``panel.test``."""
    if "." in name:
        panel_key, _, test_key = name.partition(".")
        panel = PANELS_BY_KEY.get(panel_key)
        if panel is None:
            return None
        for test in panel.tests:
            if test.key == test_key:
                return panel, test
        return None
    for panel in PANELS:
        for test in panel.tests:
            if test.key == name:
                return panel, test
    return None
