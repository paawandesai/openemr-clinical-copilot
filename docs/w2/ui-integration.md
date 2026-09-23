# Wiring the source-grounded answer view into the chart embed

**Status: not applied.** Work order 11 built the view, the PDF overlay, the
upload control and the fixture mode. Mid-session the write-back work order
(09) took ownership of **all PHP in
`interface/modules/custom_modules/oe-module-clinical-copilot`, including
`Bootstrap.php`**, so this branch wrote none. Everything below is what that
session needs to add, and the contracts the JavaScript on this branch
already calls.

**No PHP was written on `feat/w2-ui`.** There is no patch to apply — this
file is the whole handover. The card's markup (`templates/source_documents_card.html.twig`)
and its assets (`public/assets/w2/`, `public/vendor/pdfjs/`) are on this
branch and are Twig, JavaScript and CSS only.

---

## 1. What this branch added

| path | what it is |
|---|---|
| `public/assets/w2/bbox-overlay.mjs` | the coordinate transform, pure, unit tested |
| `public/assets/w2/answer-view.mjs` | claims, chips, unsupported list, D20 flag table |
| `public/assets/w2/document-viewer.mjs` | PDF.js page render + box layer |
| `public/assets/w2/upload.mjs` | file picker, document type, D7 status polling |
| `public/assets/w2/source-card.mjs` | mounts the three, reads `data-` attributes |
| `public/assets/w2/source-card.css` | all of it, scoped under `.ccw2-root` |
| `public/vendor/pdfjs/` | pdfjs-dist 6.3.289 legacy build, pinned, see `PROVENANCE.md` |
| `templates/source_documents_card.html.twig` | the card markup and its translated strings |
| `dev/` | the fixture page and the two scripted checks (development only) |

---

## 2. The three routes the JavaScript calls

`source-card.mjs` reads all three paths off the card's `data-` attributes,
so the paths are yours. **Everything else below is pinned** — the
JavaScript on this branch already parses exactly these shapes, and
`dev/fixture.html`'s canned source implements them.

### 2.0 Conventions that apply to all three

**Every response is JSON except the document bytes**, and every JSON
response uses the envelope `public/brief.php` already established:

```jsonc
// success
{"status": "ok",    ... route-specific fields ... }
// failure
{"status": "error", "error": "<BriefFailure value>", "message": "<safe to show>",
 "correlation_id": "<uuid>|null"}
```

`message` is shown to the clinician, so it must never carry exception text
or chart content. `BriefFailure::httpStatus()` already maps every case to
its status code; the table below names the case, not a new number.

> **`status` is the envelope, never the extraction state.** The extraction
> state is `extraction_status`. They are different vocabularies and both
> would otherwise land on one field — the field that decides whether the
> browser keeps polling. The agent's own responses have no envelope and
> call it `status`; **the module renames it on the way out.**

**Every request is authorised the same way**, by obtaining a `ChartRequest`
first (`src/ChartRequest.php`), which checks in this order: POST, CSRF
token, well-formed `pid`, the ACL, and that `pid` is still the patient open
in the session. `document.php` is a `GET` and so cannot use it unchanged —
see 2.1.

| failure | HTTP | `error` |
|---|---|---|
| not POST / bad or missing CSRF / malformed `pid` | 400 | `invalid_request` |
| caller lacks the ACL | 403 | `forbidden` |
| `pid` is not the patient open in the session | 409 | `patient_context_changed` |
| module settings incomplete | 503 | `not_configured` |
| token broker failed | 502 | `token_unavailable` |
| agent unreachable or non-contract reply | 502 | `agent_unavailable` |

`409 patient_context_changed` is load-bearing for the browser: on seeing it
the card stops asking for anything, because the chart it was rendered for
is no longer open (`pre-visit-brief-card.js` does the same today).

**Three new `BriefFailure` cases** are needed. Suggested values, which the
JavaScript already special-cases where it matters:

| case | value | HTTP |
|---|---|---|
| `DocumentNotFound` | `document_not_found` | 404 |
| `UploadRejected` | `upload_rejected` | 400 |
| `ExtractionNotFound` | `extraction_not_found` | 404 |

---

### 2.1 `GET <module>/document` — the PDF bytes

Replaces `GET /fhir/Binary/:id` **for clinicians**, and is the reason D28
exists:

> For a non-patient request,
> `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:285` runs
> `RestConfig::request_authorization_check($request, "admin", "users")`
> before `FhirDocumentRestController::downloadDocument()`. A physician does
> not hold `admin`/`users`, so that route answers **403 to exactly the
> people this feature is for.** The scope is not the obstacle —
> `user/Binary.read` is a real scope — the ACL is.

**This route must check `AclMain::aclCheckCore('patients', 'docs')`** — the
medical-records-documents ACL — and must check that the document belongs to
the patient open in the session. Those are two different checks and both
are required; the first is about the user, the second about the record.

| | |
|---|---|
| Method | `GET` |
| Query | `document_id` — integer, `> 0`, OpenEMR's `documents.id` |
| Query | `pid` — integer, `> 0`, the patient the card was rendered for |
| Query | `csrf_token_form` — string |

`GET`, not `POST`, because the browser fetches it as a subresource and a
`POST` for a read is a redirect away from being cacheable, bookmarkable and
`Range`-able. It therefore cannot use `ChartRequest::fromRequest()`
unchanged, which requires POST. **Either** relax that class to accept a
method argument, **or** apply the same five checks in the same order in the
route. Do not apply a weaker set.

**200**

```
Content-Type: application/pdf
Content-Disposition: inline; filename="<sanitised>"
Content-Length: <bytes>
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

Body is the file. The browser reads it with `response.arrayBuffer()` and
hands it to PDF.js; nothing else about the response is inspected.

**Failures** — a JSON envelope, as above:

| condition | HTTP | `error` | why |
|---|---|---|---|
| no such `documents.id`, or it is deleted/expired | 404 | `document_not_found` | |
| the document exists but belongs to **another patient** | **404** | `document_not_found` | **Not 403.** 403 would confirm the id exists, which tells a caller something about a chart they may not read. Indistinguishable from "no such document" on purpose. |
| caller lacks `patients`/`docs` | 403 | `forbidden` | says nothing patient-specific, so it may be distinct |
| `pid` ≠ the session's open patient | 409 | `patient_context_changed` | the card must stop asking |
| malformed or missing `document_id`/`pid`/CSRF | 400 | `invalid_request` | |

The viewer distinguishes 403 and 404 and shows different text
(`DOCUMENT_ERRORS` in `document-viewer.mjs`); every other status is
"the document could not be fetched". It never retries and never logs.

**Extraction state is irrelevant here.** The bytes exist from the moment
the file is stored, so a document whose extraction is still running is
still served, 200, with its pages drawable. There will simply be no
citations pointing into it yet.

**If `SourceDocument.document_id` is not OpenEMR's integer id** — in
fixture mode it is the scenario name — pass a mapping function as the
second argument to `moduleDocumentSource()`. The contract's
`document_id` is deliberately opaque (see the module docstring of
`agent/app/models/w2/answer_view.py`); only the module knows how an
uploaded file was registered.

---

### 2.2 `POST <module>/upload` — accept a file, start extracting

| | |
|---|---|
| Method | `POST`, `multipart/form-data` |
| Body | `pid` — integer, `> 0` |
| Body | `csrf_token_form` — string |
| Body | `document_kind` — `"lab_pdf"` \| `"intake_form"`, exactly |
| Body | `document` — the file part |

Validate before forwarding: `≤ 25 MB` (`MAX_BYTES` in `upload.mjs`,
`MAX_UPLOAD_BYTES` in the agent), and the bytes start `%PDF-`. The browser
checks both first, which is a courtesy and not a control.

**200**

```jsonc
{
  "status": "ok",
  "document_id": "<string>",          // opaque; what 2.3 and the viewer take
  "kind": "lab_pdf",                  // echoes document_kind
  "extraction_status": "pending",     // pending | in_progress | complete | failed
  "byte_length": 32032,               // integer
  "correlation_id": "<uuid>"
}
```

`extraction_status` is already meaningful on this response — **D7:
extraction runs at upload time, not at question time.**

**Re-uploading the same bytes is not an error and not a second document.**
The agent derives `document_id` from the file's SHA3-512 through
`document_idempotency_key` (D1), so an identical file returns **200** with
the same `document_id` and whatever `extraction_status` that document has
now — possibly `complete`. Invariant 6's shape, one step before the write.

**Failures:**

| condition | HTTP | `error` |
|---|---|---|
| missing/empty file part, not a PDF, unknown `document_kind` | 400 | `upload_rejected` |
| over 25 MB | 413 | `upload_rejected` |
| bad CSRF / malformed `pid` | 400 | `invalid_request` |
| lacks `patients`/`docs` | 403 | `forbidden` |
| `pid` ≠ session patient | 409 | `patient_context_changed` |
| agent unreachable or broke contract | 502 | `agent_unavailable` |

The browser shows "The upload was not accepted." plus the
`correlation_id`, re-enables the form, and does not retry by itself.

**The agent hop** is server-side, with the clinician's token, and takes the
**raw PDF as the body** rather than multipart:

```
POST <agent>/documents?kind=lab_pdf
Authorization: Bearer <the clinician's token>
Content-Type: application/pdf

<bytes>
```

It answers `{"document_id", "kind", "status", "byte_length",
"correlation_id"}` — note **`status`**, which the module renames to
`extraction_status`. Its failures are `401` (no token), `400` (empty),
`413` (too large), `415` (not a PDF / wrong content type), `422` (bad
`kind`). Raw body rather than multipart keeps `python-multipart` out of the
agent's runtime dependencies, and the only client is a server.

~~It is a **stub** today~~ — **changed by work order 13 (D40 item 8,
approved):** the route now runs the real intake-extractor at upload and
**requires `patient_id`**, the open chart's FHIR Patient uuid, as a query
parameter:

```
POST <agent>/documents?kind=lab_pdf&patient_id=<FHIR Patient uuid>
```

The agent makes the Patient read under the token before storing anything,
so the failure set gains `404 patient_not_found` (unknown patient),
`401 fhir_token_rejected` (OpenEMR refused the token), `502 fhir_unavailable`
and `409` (these exact bytes are already attached to a *different* chart —
never rebound). The module holds `pid`; it must send the uuid for it. The
response shape is unchanged.

---

### 2.3 `GET <module>/upload/status` — how far the extraction got

Path is nominal; §3 passes `.../public/upload-status.php`.

| | |
|---|---|
| Method | `GET` |
| Query | `document_id` — string, as returned by 2.2 |
| Query | `pid` — integer, `> 0` |
| Query | `csrf_token_form` — string |

**200**

```jsonc
{
  "status": "ok",
  "document_id": "<string>",
  "kind": "lab_pdf",
  "extraction_status": "in_progress",  // pending | in_progress | complete | failed
  "extraction_id": null,               // string | null; non-null only when complete
  "failure_reason": null,              // ExtractionFailureReason | null; non-null only when failed
  "correlation_id": "<uuid>"
}
```

`failure_reason` is one of `unreadable_file`, `unsupported_mime_type`,
`no_text_layer_and_ocr_failed`, `model_output_invalid`,
`page_limit_exceeded`, `timed_out`. The browser has a label for each.

**"Extraction not finished" is a 200, not a failure.** `pending` and
`in_progress` are successful reads of a job that is still running, and the
browser keeps polling: 400ms, 800ms, 1.5s, 2.5s, then every 4s, giving up
after five minutes with "still extracting; this page stopped asking" —
explicitly not a failure. A poll that errors does **not** end the run,
because a dropped request says nothing about a server-side job.

The one place unfinished extraction *is* an error is a **question** asked
against that document, and it is not one of these routes: the supervisor
answers `RoutingReasonCode.document_still_processing` (D7 — "never a
synchronous wait and never a partial extraction presented as complete").

**Failures:**

| condition | HTTP | `error` |
|---|---|---|
| no extraction recorded for `document_id` | 404 | `extraction_not_found` |
| the document belongs to **another patient** | **404** | `extraction_not_found` (same non-disclosure rule as 2.1) |
| bad CSRF / malformed `pid`/`document_id` | 400 | `invalid_request` |
| lacks `patients`/`docs` | 403 | `forbidden` |
| `pid` ≠ session patient | 409 | `patient_context_changed` |
| agent unreachable | 502 | `agent_unavailable` |

**The agent hop:** `GET <agent>/documents/{document_id}/extraction` with
the bearer token. Answers the same fields with `status` in place of
`extraction_status`; `401` without a token, `404` for an unknown document.

**Changed by work order 13 (D40 item 8):** it also requires
`?patient_id=<FHIR Patient uuid>`, makes the Patient read first, and answers
`404` — the same body as an unknown id — for a document uploaded to another
chart. `extraction_id` is now the real run's id once `complete`.
`failure_reason` can also be `vision_call_failed`, `no_fields_located` or
`internal_error` (added by the extractor, D35, in
`app/models/w2/workers.py`); the browser has no label for those three yet.

### 2.4 The ask path — `POST <agent>/w2/ask` (agent side only)

Added by work order 13; no module route calls it yet. Same authorization as
`POST /pre-visit-brief/conversation` (bearer token, Patient read first, the
conversation keyed by the token's `sub` and shared with that route). Body:

```jsonc
{"patient_id": "<FHIR uuid>", "session_id": "<8-64 [A-Za-z0-9-]>",
 "question": "<≤500 chars>", "document_ids": ["<id from POST /documents>"]}
```

200 for every clinical outcome — `status` is `answered | partially_answered
| not_in_record | refused`, `reason_code` is a `RoutingReasonCode`, `message`
is safe to show, and `answer_view` is the `AnswerView` the source card
already renders (present when the turn reached the answer step). `routing`
and `handoffs` are the inspectable supervisor trace. HTTP errors: `401`,
`404 patient_not_found`, `422`, `502 fhir_unavailable` (the authorizing
Patient read itself failed), `500`. Contract: `agent/app/models/w2/ask.py`.

## 3. Registering the card

`Bootstrap.php` currently adds one card. This is the second, and it is the
same shape. **Not applied on this branch** — it is here so the write-back
session does not have to re-derive the template variables.

```php
// Bootstrap.php: alongside CARD_ID.
public const SOURCES_CARD_ID = 'clinical_copilot_sources_ps_expand';

/** Reading an uploaded document is reading the record. */
public const DOCUMENTS_ACL = ['patients', 'docs'];

// In subscribeToEvents(), next to the existing SectionEvent listener:
//   $this->eventDispatcher->addListener(SectionEvent::EVENT_HANDLE, $this->addSourcesCard(...));

private function buildSourcesCard(): ?CardModel
{
    if (!AclMain::aclCheckCore(self::DOCUMENTS_ACL[0], self::DOCUMENTS_ACL[1])) {
        return null;
    }
    $session = SessionWrapperFactory::getInstance()->getActiveSession();
    $pid = filter_var($session->get('pid'), FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
    if ($pid === false) {
        return null;
    }
    try {
        ModuleConfig::fromGlobals($this->globals, $this->crypto);
    } catch (ModuleNotConfigured $e) {
        $this->logger->warning('Clinical Co-Pilot sources card not shown', ['setting' => $e->setting]);
        return null;
    }

    $moduleUrl = $this->globals->getWebRoot() . self::MODULE_PATH;
    return new CardModel([
        'dispatcher' => $this->eventDispatcher,
        'title' => xl('Clinical Co-Pilot: Documents & Sources'),
        'identifier' => self::SOURCES_CARD_ID,
        'acl' => self::DOCUMENTS_ACL,
        'initiallyCollapsed' => in_array(UserSettingsService::getUserSetting(self::SOURCES_CARD_ID), ['0', 0], true),
        'add' => false,
        'edit' => false,
        'collapse' => true,
        'templateFile' => '@' . self::TEMPLATE_NAMESPACE . '/source_documents_card.html.twig',
        'templateVariables' => [
            'auth' => false,
            'pid' => $pid,
            'csrfToken' => CsrfUtils::collectCsrfToken($session),
            'documentEndpoint' => $moduleUrl . '/public/document.php',
            'uploadEndpoint' => $moduleUrl . '/public/upload.php',
            'statusEndpoint' => $moduleUrl . '/public/upload-status.php',
            'stylesheetUrl' => $moduleUrl . '/public/assets/w2/source-card.css?v=' . $this->assetVersion(),
            'scriptUrl' => $moduleUrl . '/public/assets/w2/source-card.mjs?v=' . $this->assetVersion(),
            'pdfLibraryUrl' => $moduleUrl . '/public/vendor/pdfjs/pdf.min.mjs',
            'pdfWorkerUrl' => $moduleUrl . '/public/vendor/pdfjs/pdf.worker.min.mjs',
        ],
    ]);
}
```

Three things about those variables:

* **`scriptUrl` and `pdfLibraryUrl` are ES module specifiers**, used in
  `import` inside the template's `<script type="module">`. A cache-busting
  query string is fine on them. `pdfLibraryUrl` must **not** carry one:
  PDF.js resolves its worker relative to itself, and the module is loaded
  twice under two URLs if the query string differs between renders.
* **`assetVersion()`** currently stats `public/assets/pre-visit-brief-card.js`.
  It needs to stat one of the w2 assets too, or the card will serve a stale
  bundle after an edit.
* `.mjs` is served as `text/javascript` by the container's Apache
  (`/etc/apache2/mime.types` maps `js mjs`), which is what a module script
  needs. Verified on `development-easy-openemr-1`, Apache 2.4.67.

---

## 4. Token scopes

The document route reads OpenEMR's own `documents` table through the
module, under the clinician's session, so it needs no FHIR scope. If a
future route does go through FHIR, `user/Binary.read` is a real scope
(`ServerScopeListEntity::fhirResourceScopesV1()` lists `Binary`) but the
route's `admin`/`users` check is the blocker, not the scope, and
`ClinicianTokenBroker::SCOPES` would need it added and the OAuth client
re-registered (`docs/w2/LOCAL_ENV.md` §4).

---

## 5. CSP, and why PDF.js is vendored

OpenEMR sends no `script-src` policy on the patient dashboard — the only
`Content-Security-Policy` headers in the tree are `frame-ancestors 'none'`
on `interface/login/login.php` and `portal/index.php` — so a CDN would
load. It is still vendored. The reasoning is in
`public/vendor/pdfjs/PROVENANCE.md`; the short version is that a third-party
script running in a page that holds PHI is a risk a permissive policy does
not remove, and clinic installs are routinely offline.

Everything the card loads is same-origin, so adding
`default-src 'none'; script-src 'self'; worker-src 'self'; img-src 'self' data: blob:; style-src 'self'`
to these routes later is a change of policy, not of code.

---

## 6. Checking it once it is wired

The fixture page already proves the rendering and the geometry without
OpenEMR (`dev/README.md`). What only a browser in a real chart can show:

1. The card appears on the patient dashboard for a user with
   `patients`/`docs`, and does not for one without.
2. `GET <module>/document?document_id=<id>` returns the PDF for a document
   on the open patient, 403 without the ACL, 404 for an unknown id.
3. The upload control posts, and the status advances to `complete`.
4. No PHI in the browser console: open devtools, exercise every path
   including the failures, and confirm the console stays empty.
