"""
Shared base for every Week 2 contract — see docs/w2/DECISIONS.md D9.

Week 1's HTTP contracts already established the house rule
(app/models/api.py `_Contract`): forbid unknown fields, coerce nothing,
freeze the instance. Week 2 keeps it, for the same reason and one more.

The same reason: a payload that does not match the contract is rejected at
construction rather than quietly reinterpreted downstream. `extra="forbid"`
is what makes "this type has no patient identifier field" (D5) an
enforceable statement instead of a convention — a caller cannot smuggle one
in under a name the model does not declare.

The one more: Week 2 contracts are the input to a *verifier*, not just to a
renderer. `strict=True` means a value that arrives as the wrong type is a
construction error at the boundary, where it is attributable, instead of a
surprise inside the abnormal-flag computation or the idempotency key. Note
that Pydantic's strict mode still widens `int` to `float` (a JSON `1` is a
valid PDF point); it refuses string-to-number and string-to-bool, which is
the class of silent reinterpretation this is here to stop.

`frozen=True` matters here more than it did in Week 1: an ExtractedField
that could be mutated after validation could be moved from `unlocated` to
`located` without passing the validator that requires a bbox, which would
defeat CLAUDE.md invariant 4.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class W2Contract(BaseModel):
    """Base for every Week 2 contract model. See the module docstring."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


#: OpenEMR hashes uploaded documents with SHA3-512
#: (`library/classes/Document.class.php:1114`, confirmed in
#: docs/w2/spikes/writeback.md §7) and returns the digest from the document
#: listing endpoint. 512 bits is 128 lowercase hex characters.
SHA3_512_PATTERN = r"^[0-9a-f]{128}$"

#: The doc-factory records a SHA-256 of each generated PDF
#: (`document.sha256`). It is a different algorithm from OpenEMR's, so the
#: two digests are carried in separate fields and never compared — see
#: DocumentChecksums in app/models/w2/documents.py.
SHA256_PATTERN = r"^[0-9a-f]{64}$"

#: Extraction ids (D1: `procedure_result.comments` carries an extraction id
#: only). `w2x-` makes the id greppable in `Observation.note[0].text`, which
#: is the only place a client can match it: the FHIR Observation search has
#: no `note`, `_text` or `identifier` parameter, so duplicate detection
#: narrows server-side on patient+code and matches this string client-side
#: (docs/w2/spikes/writeback.md §7).
EXTRACTION_ID_PATTERN = r"^w2x-[0-9a-f]{32}$"

#: Week 1's PATIENT_ID_PATTERN (app/models/api.py), repeated rather than
#: imported so that importing a Week 2 contract does not drag in the HTTP
#: layer. OpenEMR's FHIR ids are lowercase UUIDs.
PATIENT_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

#: A correlation id as app/middleware/correlation.py generates it: a UUID4
#: string. Every Week 2 contract that crosses a node boundary carries one,
#: because it is the only identifier that may appear in a log line
#: (CLAUDE.md invariant 1).
CORRELATION_ID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
