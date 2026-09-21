"""
Chart source: read-only pull from the LOCAL OpenEMR instance, plus a local cache.

The factory needs a patient's demographics, medications, allergies and
conditions so that a generated document matches the chart by default —
perturbations are the only intended differences. This module is the only place
that talks to OpenEMR, and it only ever issues ``SELECT`` statements inside a
``READ ONLY`` session.

Every pull is written to ``evals/w2/factory/cache/patient-<ref>.json``, and the
cache is preferred on every subsequent run, so regenerating documents (and the
determinism tests) does not depend on OpenEMR being up. ``--refresh`` forces a
new pull.

Connection settings come from the environment, falling back to agent/.env and
then to the published defaults of ``docker/development-easy`` (the local dev
stack's MySQL is bound to 127.0.0.1:18320). Credentials are never logged: the
only thing this module prints is the patient_ref, which is an opaque synthetic
id, and never a name, DOB, address or phone number.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CHART_CACHE_SCHEMA_VERSION = "doc-factory-chart-cache/0.1.0"

#: Synthea encodes drug strength in the product name, e.g.
#: "Loratadine 5 MG Chewable Tablet" -> "5 MG". OpenEMR's prescriptions.dosage
#: for this data set is a bare "1.00" with no unit, so the name is the only
#: place the strength actually lives.
_STRENGTH_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(MG/ACTUAT|MG/ML|MCG/ACTUAT|MCG|MG|ML|UNT|%)(?![\w])",
    re.IGNORECASE,
)


class ChartUnavailable(RuntimeError):
    """No cache entry and OpenEMR could not be read."""


@dataclass(frozen=True)
class ChartMedication:
    name: str
    strength: str | None
    started: str | None


@dataclass(frozen=True)
class ChartAllergy:
    allergen: str
    reaction: str | None
    severity: str | None


@dataclass(frozen=True)
class ChartCondition:
    name: str
    code: str | None
    onset: str | None


@dataclass(frozen=True)
class ChartRecord:
    """The subset of a chart the factory renders from."""

    patient_ref: str
    mrn: str
    first_name: str
    middle_name: str
    last_name: str
    dob: str
    sex: str
    street: str
    city: str
    state: str
    postal_code: str
    phone: str
    medications: tuple[ChartMedication, ...] = ()
    allergies: tuple[ChartAllergy, ...] = ()
    conditions: tuple[ChartCondition, ...] = ()

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    @property
    def city_state_zip(self) -> str:
        head = ", ".join(p for p in (self.city, self.state) if p)
        return " ".join(p for p in (head, self.postal_code) if p).strip()

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ChartRecord":
        return cls(
            **{k: v for k, v in raw.items() if k not in ("medications", "allergies", "conditions")},
            medications=tuple(ChartMedication(**m) for m in raw.get("medications", [])),
            allergies=tuple(ChartAllergy(**a) for a in raw.get("allergies", [])),
            conditions=tuple(ChartCondition(**c) for c in raw.get("conditions", [])),
        )


def extract_strength(product_name: str) -> str | None:
    """"Loratadine 5 MG Chewable Tablet" -> "5 MG"; None when the name has none."""
    match = _STRENGTH_RE.search(product_name)
    if match is None:
        return None
    return f"{match.group(1)} {match.group(2).upper()}"


# --------------------------------------------------------------------------
# Connection settings
# --------------------------------------------------------------------------

def _load_dotenv(env_path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE reader. Values are returned, never logged."""
    if not env_path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    def __str__(self) -> str:  # never interpolate the password into output
        return f"{self.database}@{self.host}:{self.port}"


#: Published defaults of docker/development-easy (MYSQL_ROOT_PASSWORD: root,
#: mysql bound to 127.0.0.1:18320). Overridable per key via the environment.
_DEFAULTS = {
    "FACTORY_OPENEMR_DB_HOST": "127.0.0.1",
    "FACTORY_OPENEMR_DB_PORT": "18320",
    "FACTORY_OPENEMR_DB_NAME": "openemr",
    "FACTORY_OPENEMR_DB_USER": "root",
    "FACTORY_OPENEMR_DB_PASSWORD": "root",
}


def db_settings(env_path: Path | None = None) -> DbSettings:
    dotenv = _load_dotenv(env_path) if env_path is not None else {}

    def get(key: str) -> str:
        return os.environ.get(key) or dotenv.get(key) or _DEFAULTS[key]

    return DbSettings(
        host=get("FACTORY_OPENEMR_DB_HOST"),
        port=int(get("FACTORY_OPENEMR_DB_PORT")),
        database=get("FACTORY_OPENEMR_DB_NAME"),
        user=get("FACTORY_OPENEMR_DB_USER"),
        password=get("FACTORY_OPENEMR_DB_PASSWORD"),
    )


# --------------------------------------------------------------------------
# Read-only pull
# --------------------------------------------------------------------------

_DEMOGRAPHICS_SQL = """
    SELECT pid, pubpid, fname, mname, lname, DOB, sex,
           street, city, state, postal_code, phone_home, phone_cell
    FROM patient_data
    WHERE {predicate}
    LIMIT 1
"""

_LIST_SQL = """
    SELECT title, diagnosis, begdate, reaction, severity_al
    FROM lists
    WHERE pid = %s AND type = %s AND (activity = 1 OR activity IS NULL)
    ORDER BY COALESCE(begdate, '1900-01-01') ASC, id ASC
"""

_OPTION_SQL = """
    SELECT list_id, option_id, title
    FROM list_options
    WHERE list_id IN ('reaction', 'severity_ci')
"""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date_only(value: Any) -> str | None:
    text = _clean(value)
    if not text or text.startswith("0000"):
        return None
    return text[:10]


def fetch_chart(patient_ref: str, settings: DbSettings) -> ChartRecord:
    """Read one patient from local OpenEMR. SELECT-only, in a READ ONLY session."""
    try:
        import pymysql
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ChartUnavailable("pymysql is not installed; see agent/requirements.txt") from exc

    try:
        connection = pymysql.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            database=settings.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as ChartUnavailable
        raise ChartUnavailable(
            f"could not connect to local OpenEMR ({settings}); is docker/development-easy up?"
        ) from exc

    try:
        with connection:
            with connection.cursor() as cursor:
                # Belt and braces: any accidental write raises instead of landing.
                cursor.execute("SET SESSION TRANSACTION READ ONLY")

                if patient_ref.isdigit():
                    predicate, params = "pid = %s", (int(patient_ref),)
                elif len(patient_ref) == 32 and all(c in "0123456789abcdefABCDEF" for c in patient_ref):
                    predicate, params = "HEX(uuid) = %s", (patient_ref.upper(),)
                else:
                    predicate, params = "pubpid = %s", (patient_ref,)

                cursor.execute(_DEMOGRAPHICS_SQL.format(predicate=predicate), params)
                demographics = cursor.fetchone()
                if demographics is None:
                    raise ChartUnavailable(f"no patient in local OpenEMR for patient_ref={patient_ref!r}")

                pid = int(demographics["pid"])

                cursor.execute(_OPTION_SQL)
                # MySQL matched list_id case-insensitively, and this data set
                # really does carry both a 'Reaction' and a 'reaction' list, so
                # the key has to be folded to match.
                options = {
                    (str(r["list_id"]).lower(), str(r["option_id"])): _clean(r["title"])
                    for r in cursor.fetchall()
                }

                cursor.execute(_LIST_SQL, (pid, "medication"))
                medication_rows = cursor.fetchall()
                cursor.execute(_LIST_SQL, (pid, "allergy"))
                allergy_rows = cursor.fetchall()
                cursor.execute(_LIST_SQL, (pid, "medical_problem"))
                condition_rows = cursor.fetchall()
    except ChartUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as ChartUnavailable
        raise ChartUnavailable(f"failed reading patient_ref={patient_ref!r} from local OpenEMR") from exc

    medications = tuple(
        ChartMedication(
            name=_clean(row["title"]),
            strength=extract_strength(_clean(row["title"])),
            started=_date_only(row["begdate"]),
        )
        for row in medication_rows
        if _clean(row["title"])
    )
    allergies = tuple(
        ChartAllergy(
            allergen=_clean(row["title"]),
            reaction=options.get(("reaction", _clean(row["reaction"]))) or None,
            severity=options.get(("severity_ci", _clean(row["severity_al"])))
            or options.get(("severity_ci_al", _clean(row["severity_al"])))
            or None,
        )
        for row in allergy_rows
        if _clean(row["title"])
    )
    conditions = tuple(
        ChartCondition(
            name=_clean(row["title"]),
            code=_clean(row["diagnosis"]) or None,
            onset=_date_only(row["begdate"]),
        )
        for row in condition_rows
        if _clean(row["title"])
    )

    return ChartRecord(
        patient_ref=patient_ref,
        mrn=_clean(demographics["pubpid"]) or str(pid),
        first_name=_clean(demographics["fname"]),
        middle_name=_clean(demographics["mname"]),
        last_name=_clean(demographics["lname"]),
        dob=_date_only(demographics["DOB"]) or "",
        sex=_clean(demographics["sex"]),
        street=_clean(demographics["street"]),
        city=_clean(demographics["city"]),
        state=_clean(demographics["state"]),
        postal_code=_clean(demographics["postal_code"]),
        phone=_clean(demographics["phone_home"]) or _clean(demographics["phone_cell"]),
        medications=medications,
        allergies=allergies,
        conditions=conditions,
    )


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def cache_path(cache_dir: Path, patient_ref: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", patient_ref)
    return cache_dir / f"patient-{safe}.json"


def write_cache(cache_dir: Path, record: ChartRecord) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, record.patient_ref)
    payload = {
        "cache_schema_version": CHART_CACHE_SCHEMA_VERSION,
        "source": "local-openemr-mysql",
        "record": record.to_json(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_cache(cache_dir: Path, patient_ref: str) -> ChartRecord | None:
    path = cache_path(cache_dir, patient_ref)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("cache_schema_version")
    if version != CHART_CACHE_SCHEMA_VERSION:
        raise ChartUnavailable(
            f"{path}: cache_schema_version {version!r} != {CHART_CACHE_SCHEMA_VERSION!r}; "
            "re-run generate with --refresh"
        )
    return ChartRecord.from_json(payload["record"])


def load_chart(
    patient_ref: str,
    cache_dir: Path,
    *,
    refresh: bool = False,
    env_path: Path | None = None,
) -> tuple[ChartRecord, str]:
    """
    Return ``(record, origin)`` where origin is ``"cache"`` or ``"openemr"``.

    The cache wins unless ``refresh`` is set. A refresh that cannot reach
    OpenEMR falls back to the cache rather than failing the run.
    """
    if not refresh:
        cached = read_cache(cache_dir, patient_ref)
        if cached is not None:
            return cached, "cache"

    try:
        record = fetch_chart(patient_ref, db_settings(env_path))
    except ChartUnavailable:
        cached = read_cache(cache_dir, patient_ref)
        if cached is not None:
            return cached, "cache"
        raise

    write_cache(cache_dir, record)
    return record, "openemr"
