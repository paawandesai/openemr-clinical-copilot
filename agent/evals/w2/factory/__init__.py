"""
Synthetic clinical-document factory (W2).

Turns a YAML scenario spec into a realistic lab PDF or intake form for an
existing synthetic OpenEMR patient, plus exact ground truth — value, page and
bounding box — for every field it renders.

Entry point is the CLI:

    cd agent
    python -m evals.w2.factory generate evals/w2/factory/specs
    python -m evals.w2.factory validate evals/w2/factory/output/samples

See evals/w2/factory/README.md for the spec format, the ground-truth format and
the coordinate convention.
"""

from evals.w2.factory.groundtruth import GROUND_TRUTH_SCHEMA_VERSION

__all__ = ["GROUND_TRUTH_SCHEMA_VERSION"]
