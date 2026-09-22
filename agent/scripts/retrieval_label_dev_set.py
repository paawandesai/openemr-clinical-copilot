"""
Resolve the hand-written relevance judgments into chunk ids and rewrite
`agent/evals/w2/staging/retrieval_dev.jsonl`.

Judgments are recorded as `(guideline_id, distinctive phrase)` rather than
as raw chunk ids, because a chunk id is a hash of the chunk's text: any
change to the chunker or to a fetched document renumbers every id, and a
label file full of stale hashes fails silently — it just scores zero and
looks like a retrieval regression. A phrase survives a rebuild, and when
it does not, `--check` says which judgment lost its chunk.

Every phrase must match exactly one chunk in its guideline. Zero matches
and two matches are both errors: an ambiguous judgment is a judgment about
something other than what was read.

    python scripts/retrieval_label_dev_set.py            # rewrite the JSONL
    python scripts/retrieval_label_dev_set.py --check    # verify, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.build import load_index  # noqa: E402

DEV_PATH = Path(__file__).resolve().parents[1] / "evals/w2/staging/retrieval_dev.jsonl"

#: query_id -> [(guideline_id, phrase that identifies the relevant chunk)]
#:
#: Judged by reading the pooled top-8 of BM25 and the dense retriever for
#: each query (scripts/retrieval_pool_candidates.py), plus, where a reading
#: of the corpus turned up an obviously relevant chunk that neither
#: retriever surfaced, that chunk. A few of those exist and they are the
#: interesting ones: q19's levothyroxine chunk is the answer to its query
#: and neither retriever put it in the top 8.
JUDGMENTS: dict[str, list[tuple[str, str]]] = {
    "q01-a1c-diagnostic-threshold": [
        ("niddk-a1c-test", "Prediabetes 5.7 to 6.4 percent"),
        ("cdc-diabetes-testing", "The A1C test measures your average blood sugar level"),
        ("uspstf-diabetes-screening", "an HbA1c level of 6.5% or greater"),
    ],
    "q02-a1c-retest-interval": [
        ("niddk-a1c-test", "have an A1C test at least twice a year"),
    ],
    "q03-a1c-unreliable": [
        ("niddk-a1c-test", "you could have a different type of hemoglobin"),
        ("niddk-a1c-test", "a blood glucose test may show diabetes when an A1C test does not"),
        ("niddk-a1c-test", "small changes in temperature"),
        ("niddk-a1c-test", "Lab test results can vary from day to day"),
        ("niddk-a1c-test", "the A1C test result can be slightly higher or lower"),
    ],
    "q04-diabetes-screening-age": [
        (
            "uspstf-diabetes-screening",
            "Adults aged 35 to 70 years who have overweight or obesity: - Screen",
        ),
        (
            "uspstf-diabetes-screening",
            "Population Recommendation Grade Asymptomatic adults aged 35 to 70 years",
        ),
        (
            "uspstf-diabetes-screening",
            "applies to nonpregnant adults aged 35 to 70 years seen in primary care",
        ),
        ("uspstf-diabetes-screening", "consider screening at an earlier age"),
    ],
    "q05-a1c-average-glucose": [
        ("niddk-a1c-test", "Estimated average glucose (eAG) is calculated from your A1C"),
    ],
    "q06-statin-primary-prevention-eligibility": [
        (
            "uspstf-statin-primary-prevention",
            "For adults aged 40 to 75 years who have 1 or more cardiovascular risk factors",
        ),
        (
            "uspstf-statin-primary-prevention",
            "Population Recommendation Grade Adults aged 40 to 75 years",
        ),
        (
            "uspstf-statin-primary-prevention",
            "(USPSTF) concludes with moderate certainty that statin use",
        ),
        (
            "uspstf-statin-primary-prevention",
            "and mortality in adults aged 40 to 75 years with no history of CVD",
        ),
        (
            "uspstf-statin-primary-prevention",
            "current evidence is insufficient to assess the balance of benefits and harms of initiating a statin",
        ),
        (
            "uspstf-statin-primary-prevention",
            "participants in clinical trials of statin therapy were enrolled based on the presence",
        ),
    ],
    "q07-ldl-levels": [
        ("cdc-cholesterol", "total cholesterol above 200 mg/dL may be considered high"),
        ("cdc-cholesterol", "Cholesterol is measured in milligrams per deciliter"),
        ("nhlbi-blood-cholesterol", "Measurement: Plasma total and HDL cholesterol"),
    ],
    "q08-statin-harms": [
        ("uspstf-statin-primary-prevention", "reviewed 19 trials (n = 75,005)"),
        (
            "uspstf-statin-primary-prevention",
            "association between statins and renal or cognitive harms",
        ),
        ("uspstf-statin-primary-prevention", "Six trials (n = 59,083)"),
        (
            "uspstf-statin-primary-prevention",
            "potential harms of statin use for the primary prevention of CVD events specifically in adult",
        ),
        (
            "nhlbi-blood-cholesterol",
            "Learn some tips to stay safe if your healthcare provider gives you statins",
        ),
    ],
    "q09-lifestyle-lowers-ldl": [
        ("nhlbi-blood-cholesterol", "To help you lower your LDL cholesterol level"),
        ("nhlbi-blood-cholesterol", "Quit smoking. Visit Smoking and Your Heart"),
        ("nhlbi-blood-cholesterol", "The TLC guide is a set of tools"),
    ],
    "q10-hypertension-definition": [
        (
            "nhlbi-high-blood-pressure",
            "For most adults, a healthy blood pressure is less than 120/80 mm Hg",
        ),
        ("nhlbi-high-blood-pressure", "A healthy systolic blood pressure is less than 120 mm Hg"),
    ],
    "q11-confirm-bp-out-of-office": [
        (
            "uspstf-hypertension-screening",
            "Initial screening for hypertension should be performed with office blood pressure measurement",
        ),
        ("uspstf-hypertension-screening", "There are different types of hypertension"),
        (
            "uspstf-hypertension-screening",
            "Centers for Medicare & Medicaid Services added coverage of ABPM",
        ),
        (
            "uspstf-hypertension-screening",
            "Take blood pressure measurements outside of the clinical setting",
        ),
        ("uspstf-hypertension-screening", "Eighteen studies (n = 57,128)"),
        (
            "nhlbi-high-blood-pressure",
            "two or more blood pressure readings at separate medical appointments",
        ),
        ("nhlbi-high-blood-pressure", "refers to blood pressure readings that are higher in"),
    ],
    "q12-bp-measurement-technique": [
        ("nhlbi-high-blood-pressure", "age 3 or older should have their blood pressure checked"),
        ("nhlbi-high-blood-pressure", "refers to blood pressure readings that are higher in"),
        (
            "uspstf-hypertension-screening",
            "Initial screening for hypertension should be performed with office blood pressure measurement",
        ),
    ],
    "q13-bp-medicines": [
        (
            "nhlbi-high-blood-pressure",
            "When healthy lifestyle changes alone do not control or lower high blood pressure",
        ),
        ("nhlbi-high-blood-pressure", "Diuretics remove extra water and sodium"),
        # Was the video caption on the treatment page; that block carried a
        # third-party animation copyright and is stripped at fetch time now,
        # so the judgment points at the treatment overview instead.
        ("nhlbi-high-blood-pressure", "work with you to develop a treatment plan"),
        (
            "nhlbi-high-blood-pressure",
            "may also recommend medicines to help control your blood pressure",
        ),
    ],
    "q14-egfr-ckd-threshold": [
        (
            "niddk-evaluate-ckd",
            "Staging systems for chronic disease should identify risk for progression",
        ),
        (
            "niddk-evaluate-ckd",
            "Urine and blood tests are used to detect and monitor kidney disease",
        ),
        ("niddk-ckd-tests-diagnosis", "use a blood test to check your kidney function"),
    ],
    "q15-creatinine-meaning": [
        (
            "medlineplus-creatinine-test",
            "This test measures creatinine levels in a sample of your blood",
        ),
        (
            "medlineplus-creatinine-test",
            "High blood creatinine levels don't always mean you have a kidney problem",
        ),
        ("medlineplus-creatinine-test", "A creatinine test is used to help"),
    ],
    "q16-urine-albumin-creatinine": [
        ("niddk-ckd-tests-diagnosis", "may check your urine for albumin"),
        ("niddk-ckd-tests-diagnosis", "repeat the urine test"),
        ("niddk-evaluate-ckd", "urine albumin-to-creatinine ratio (UACR)"),
        ("niddk-evaluate-ckd", "two key markers for CKD are urine albumin and eGFR"),
    ],
    "q17-ckd-staging": [
        (
            "niddk-evaluate-ckd",
            "Staging systems for chronic disease should identify risk for progression",
        ),
        (
            "niddk-evaluate-ckd",
            "GFR may be too narrow a basis on which to assess risk for progression",
        ),
        ("niddk-ckd-tests-diagnosis", "use a blood test to check your kidney function"),
    ],
    "q18-tsh-hypothyroid": [
        ("uspstf-thyroid-screening", "Subclinical hypothyroidism is defined"),
        ("uspstf-thyroid-screening", "Subclinical hyperthyroidism is defined"),
        ("uspstf-thyroid-screening", "The serum TSH test is the primary screening test"),
        ("medlineplus-tsh-test", "A TSH test is used to find out how well your thyroid is working"),
        ("medlineplus-tsh-test", "if you have symptoms of too much or too little thyroid hormone"),
        (
            "medlineplus-tsh-test",
            "Your TSH level may be high or low even when your thyroid gland is healthy",
        ),
    ],
    "q19-levothyroxine-followup": [
        ("niddk-hypothyroidism", "You will take levothyroxine"),
    ],
    "q20-hyperthyroid-symptoms": [
        ("niddk-hyperthyroidism", "Symptoms of hyperthyroidism can vary"),
        (
            "niddk-hyperthyroidism",
            "also called overactive thyroid, is when the thyroid gland makes more",
        ),
    ],
    "q21-thyroid-screening-asymptomatic": [
        (
            "uspstf-thyroid-screening",
            "evidence is insufficient and that the balance of benefits and harms of scre",
        ),
        (
            "uspstf-thyroid-screening",
            "inadequate evidence that screening for thyroid dysfunction in nonpregnant",
        ),
        (
            "uspstf-thyroid-screening",
            "no direct evidence on the benefits and harms of screening for thyroid dysfunction",
        ),
        ("uspstf-thyroid-screening", "No recommendation. Grade: I statement"),
        (
            "uspstf-thyroid-screening",
            "Early detection and treatment of asymptomatic persons with abnormal serum TSH",
        ),
    ],
    "q22-ferritin-iron-studies": [
        (
            "uspstf-iron-deficiency-anemia-pregnancy",
            "- Screening for iron deficiency and iron deficiency anemia often includes",
        ),
        (
            "uspstf-iron-deficiency-anemia-pregnancy",
            "Iron is also necessary for the production of additional proteins",
        ),
        ("uspstf-iron-deficiency-anemia-pregnancy", "with hemoglobin, hematocrit, or fer"),
        ("nhlbi-iron-deficiency-anemia", "check your complete blood count (CBC)"),
    ],
    "q23-iron-in-pregnancy": [
        (
            "uspstf-iron-deficiency-anemia-pregnancy",
            "For asymptomatic pregnant adolescents and adults: The current eviden",
        ),
        (
            "uspstf-iron-deficiency-anemia-pregnancy",
            "aim of routine screening or iron supplementation for treatment",
        ),
        (
            "uspstf-iron-deficiency-anemia-pregnancy",
            "inconsistent evidence on maternal iron supplementation during pregnancy",
        ),
        (
            "uspstf-iron-deficiency-anemia-pregnancy",
            "recommends universal supplementation for all pregnant",
        ),
        (
            "uspstf-iron-deficiency-anemia-pregnancy",
            "reported on maternal iron deficiency anemia with supplementat",
        ),
    ],
    "q24-iron-deficiency-causes": [
        ("nhlbi-iron-deficiency-anemia", "Conditions that increase your risk of iron-def"),
        (
            "nhlbi-iron-deficiency-anemia",
            "Other conditions that may cause iron-deficiency anemia include",
        ),
        ("nhlbi-iron-deficiency-anemia", "decrease your body"),
        ("nhlbi-iron-deficiency-anemia", "When you lose blood, you lose iron"),
    ],
    "q25-vitamin-d-test-meaning": [
        ("medlineplus-vitamin-d-test", "Your test results may be reported as"),
        (
            "medlineplus-vitamin-d-test",
            "A vitamin D test measures the levels of vitamin D in your blood",
        ),
        ("medlineplus-vitamin-d-test", "To increase your vitamin D levels"),
        (
            "uspstf-vitamin-d-deficiency-screening",
            "Total 25(OH)D levels can be measured by both binding and chemical assays",
        ),
    ],
    "q26-vitamin-d-screening": [
        (
            "uspstf-vitamin-d-deficiency-screening",
            "Asymptomatic, community-dwelling, nonpregnant adults",
        ),
        (
            "uspstf-vitamin-d-deficiency-screening",
            "For asymptomatic, community-dwelling, nonpregnant adults: The USPSTF found",
        ),
        (
            "uspstf-vitamin-d-deficiency-screening",
            "recommendation applies to community-dwelling, nonpregnant adults",
        ),
        (
            "uspstf-vitamin-d-deficiency-screening",
            "concludes that the overall evidence on the benefits o",
        ),
        (
            "uspstf-vitamin-d-deficiency-screening",
            "treatment of asymptomatic vitamin D deficiency has no benefit",
        ),
        (
            "uspstf-vitamin-d-deficiency-screening",
            "goal of screening for vitamin D deficiency would be",
        ),
    ],
    "q27-vitamin-d-calcium-fractures": [
        ("uspstf-vitamin-d-calcium-fractures", "recommends against daily supplementa"),
        (
            "uspstf-vitamin-d-calcium-fractures",
            "reviewed evidence from 8 randomized clinical trials",
        ),
        (
            "uspstf-vitamin-d-calcium-fractures",
            "inadequate evidence to estimate the benefits of vitamin D, calcium",
        ),
        ("uspstf-vitamin-d-calcium-fractures", "Health Initiative (WHI) trial (n=36,282)"),
        (
            "uspstf-vitamin-d-calcium-fractures",
            "inadequate evidence to determine the effects of vitamin D and calcium supplementati",
        ),
        (
            "uspstf-vitamin-d-calcium-fractures",
            "Two studies reported on the effect of calcium supplementation on fracture prevention",
        ),
    ],
    "q28-medication-list": [
        ("nia-taking-medicines-safely", "it can be challenging to keep track of everything"),
        ("cdc-medication-safety", "Keep a list of your medicines, vitamins"),
        ("nia-taking-medicines-safely", "tell them about any new prescription drugs"),
    ],
    "q29-adverse-drug-events": [
        ("cdc-medication-safety", "An adverse drug event (ADE) is when a medication causes harm"),
        ("cdc-medication-safety", "The number of ADEs can increase due to"),
        ("nia-taking-medicines-safely", "are called side effect"),
    ],
    "q30-polypharmacy-older-adults": [
        ("nia-taking-medicines-safely", "called polypharmacy"),
        ("nia-taking-medicines-safely", "taking medicines the wrong"),
        ("nia-taking-medicines-safely", "changes in your body can affec"),
    ],
}


def resolve(index, guideline_id: str, phrase: str) -> str:
    # Whitespace-normalised on both sides: a chunk joins its paragraphs with
    # a newline, so a phrase that spans a paragraph break — which is most of
    # the "Population / Recommendation / Grade" boilerplate on a USPSTF page
    # — would otherwise never match its own text.
    needle = " ".join(phrase.split())
    matches = [
        c.chunk_id
        for c in index.chunks
        if c.guideline_id == guideline_id and needle in " ".join(c.text.split())
    ]
    if len(matches) != 1:
        raise LookupError(
            f"{guideline_id}: phrase {phrase!r} matched {len(matches)} chunks, expected 1"
        )
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve dev-set relevance judgments.")
    ap.add_argument("--check", action="store_true", help="verify only, write nothing")
    args = ap.parse_args(argv)

    index = load_index()
    rows = [json.loads(line) for line in DEV_PATH.read_text(encoding="utf-8").splitlines() if line]

    problems: list[str] = []
    total = 0
    for row in rows:
        judgments = JUDGMENTS.get(row["query_id"])
        if not judgments:
            problems.append(f"{row['query_id']}: no judgments recorded")
            continue
        resolved: list[str] = []
        for guideline_id, phrase in judgments:
            try:
                resolved.append(resolve(index, guideline_id, phrase))
            except LookupError as exc:
                problems.append(f"{row['query_id']}: {exc}")
        # Sorted so the file is stable, de-duplicated so a phrase written
        # twice does not inflate the denominator of recall.
        row["relevant_chunk_ids"] = sorted(set(resolved))
        total += len(row["relevant_chunk_ids"])

    if problems:
        for problem in problems:
            print(f"UNRESOLVED {problem}", file=sys.stderr)
        return 1

    if args.check:
        current = [
            json.loads(line) for line in DEV_PATH.read_text(encoding="utf-8").splitlines() if line
        ]
        stale = [
            r["query_id"]
            for r, c in zip(rows, current, strict=True)
            if r["relevant_chunk_ids"] != c["relevant_chunk_ids"]
        ]
        if stale:
            print(f"STALE labels for: {', '.join(stale)}")
            return 1
        print(f"labels up to date: {total} judgments over {len(rows)} queries")
        return 0

    with DEV_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{total} judgments over {len(rows)} queries -> {DEV_PATH.name}")
    print(f"mean relevant per query: {total / len(rows):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
