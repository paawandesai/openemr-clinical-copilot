# Patient dashboard port: Phase 1 parity inventory

Work order 31, branch `feat/w2-dashboard-port`. Written 2026-09-24 against the
local stack (`development-easy`, OpenEMR 8.x dev, Vultr DB restore, 52
synthetic patients). Everything here was read from the running PHP page, from
live FHIR responses, and from the PHP and FHIR source. Where these disagree,
the running system wins, and the disagreement is written down.

Patients used (synthetic only; identified by UUID, per the local-DB note):

| pid | name | UUID | why |
|---|---|---|---|
| 20 | Dee580 Homenick806 | `a2c373a4-dd96-48f7-b353-6732b98bf04f` | Synthea. 7 allergies, 26 problems (2 active), 7 lists-meds, 7 prescriptions, 22 lab results |
| 1 | Phil Belford | `a2c36e54-af42-4c38-b86e-ade616ed5494` | Hand-entered demo patient. Uncoded allergy, no prescriptions, no labs |
| 16 | Colby655 Gerlach374 | `a2c3739a-29d3-4664-bac5-00d202aba3a5` | Synthea. 3 allergies, 18 problems (7 active), 30 lab results |

Screenshots: `inventory/php-pid{20,1,16}-header.png` (the patient bar in the
tabbed UI) and `inventory/php-pid{20,1,16}-cards.png` (the dashboard frame,
every card forced open). Physician login, 1440 px wide.

---

## 1. What the PHP dashboard shows

The dashboard is two pieces. The **patient bar** is drawn by the tabbed shell
(`interface/main/tabs/templates/patient_data_template.php`, a Knockout
template fed by `demographics.php:924` via `left_nav.setPatient`). The
**cards** are drawn in the dashboard frame by `demographics.php:1087-1330`
with Twig templates under `templates/patient/card/`. Every card is a
Bootstrap 4.6.2 `.card` from `card_base.html.twig`: a blue bold title link
that toggles collapse (`fa-compress` / `fa-expand` icon), an edit or add
pencil at the right when the user has write ACL, and a `list-group
list-group-flush` body.

### 1.1 Patient bar (header)

| Element | Shown as | Source |
|---|---|---|
| Photo | 75x75 `img-thumbnail`, default silhouette when none | patient documents |
| Name | `fname lname`, large link | `patient_data` |
| MRN | `(pubpid)`, muted, after the name | `patient_data.pubpid` |
| DOB + age | `DOB: 2004-08-24 Age: 22` (global date format; `Age at death` if deceased) | `demographics.php:927-929` |
| Sex | **not in the bar.** Shown in the Demographics card as `Birth Sex` and `Sex` | `patient_data.sex`, `sex_identified` |
| Active status | **no "active" field exists in the PHP UI.** The only status shown is a red `Deceased - <date> (<n> days ago)` banner card | `is_patient_deceased()`, `partials/deceased.html.twig` |
| Encounter picker, new encounter, visit history | buttons | out of scope (write actions, encounters) |

### 1.2 Cards in scope, in page order

Row 1 is Allergies, Medical Problems, Medications at `col-md-4` each (the
column width is `12 / number of visible cards`). Prescriptions spans the
next row. Care Team is full width on the row after.

**Allergies** (`card/allergies.html.twig`)
- Rows: `title (severity)`. Severity is `getListItemTitle('severity_ccda',
  severity_al)`. When the code is not in the list the raw value prints, so
  Synthea rows show `(1)` and Phil Belford shows `penicillin ()`.
- Severity `severe`, `life_threatening_severity` or `fatal` is highlighted
  `bg-warning font-weight-bold`.
- Hover title: `<title> Reaction: <reaction title> - <severity>`.
- Filter: `outcome != 1` and (`enddate` empty or in the future).
- Order: service order (by `lists.id`).
- Empty: `No Known Allergies` if the list was ever touched, else
  `Nothing Recorded`.
- Action: pencil opens `stats_full.php?category=allergy` (write).

**Medical Problems** (`card/medical_problems.html.twig`)
- Rows: `title` only.
- Filter: same active filter. Order: `lists.begdate` ascending.
- Empty: `None` if touched, else `Nothing Recorded`.

**Medications** (`card/medication.html.twig`), the `lists` table,
`type = medication`
- Rows: `title` then `drug_dosage_instructions`.
- Filter: same active filter. Order: `begdate` ascending.
- Empty: `None` if touched, else `Nothing Recorded`.

**Prescriptions** (`controllers/C_Prescription` `fragment` action,
`templates/prescription/general_fragment.html`), the `prescriptions` table
- Table columns `Drug | Details | Qty | Refills | Filled`. Details =
  `size` + unit + `get_dosage_display()` (`dosage`, plus "in <form>
  <interval>" when set). Filled = `date_added` as `YYYY-MM-DD HH:MM:SS`.
- Filter: `active > 0` only. Order: `active DESC, date_modified DESC,
  date_added DESC`.
- Empty: `None`.
- Hidden entirely when `disable_prescriptions` is set or the user lacks
  `patients/rx`.

**Care Team** (`card/manage_care_team.html.twig`)
- Team name heading with a status badge, then a table: `Type` (Provider /
  Related person badge) | `Member` | `Role` | `Facility` | `Since` | `Status`
  | `Note` (| `Remove`, edit mode only).
- Empty: header row only, no text. **No care team exists for any of the 52
  local patients** (`care_teams` and `care_team_member` are empty), so every
  screenshot shows the empty table.
- Edit mode (inline form, POST) is a write action and out of scope.

**Labs** (the additional section; `labdata_fragment.php`)
- The PHP card shows only the **most recent** procedure: `Procedure: <name>
  (<date>)`, `Encounter: <id>`, and a link to `labdata.php` to view and graph
  everything. Empty: `No lab data documented.`
- The requirement's lab section (value, unit, range, flag, date, per-analyte
  trend) therefore matches `labdata.php`, not the card. The port shows the
  results inline, which is closer to the requirement than to the card. This
  is a stated difference, not parity.

Cards on the page but **not in scope**: Treatment Intervention Preferences,
Care Experience Preferences, Clinical Co-Pilot cards, Demographics, Billing,
Insurance, Messages, Patient Reminders, Disclosures, Amendments, Vitals,
Portal, Clinical Reminders, Recall, Appointments, Health Concerns,
Immunizations.

---

## 2. Field map: PHP to FHIR, verified against live responses

Token: physician, password grant on a local inventory client, all FHIR reads
200 in 0.17-0.45 s each. The responses were compared with the PHP page for
the same three patients.

### Patient

| PHP | FHIR | Verified |
|---|---|---|
| name | `name[use=official]`: `given` joined + `family` | match, all 3 |
| MRN (pubpid) | **`identifier` where `type.coding.code = "PT"`** (system `v2-0203`). The other identifier is `SS` (Social Security number) and must never be shown as the MRN | match, all 3 |
| DOB | `birthDate` | match |
| Age | computed client side from `birthDate` | derivable |
| Sex | `gender` (from `patient_data.sex`), plus `extension[us-core-birthsex].valueCode` | match (`male` / `M`) |
| Deceased banner | `deceasedBoolean` / `deceasedDateTime` | present (`false` for all 52) |
| Active status | `active` | **hardcoded `true`** (`FhirPatientService.php:212`). It carries no information |

### AllergyIntolerance (`?patient=`)

| PHP | FHIR | Verified |
|---|---|---|
| title | `code.coding[].display`; for an uncoded allergy **only `text.div`** (Phil Belford: `code` is the data-absent-reason `unknown`, name only in the narrative) | match |
| active filter | `clinicalStatus`: `active` iff `enddate` is null; `resolved` iff outcome 1 and an end date; else `inactive` (`FhirAllergyIntoleranceService.php:117`) | equal on all 3; differs at the edges (below) |
| severity text | `criticality`, lossy: mild, moderate -> `low`; moderate-to-severe, severe, life-threatening, fatal -> `high`; unassigned -> `unable-to-assess`; an unknown code (Synthea's `1`) -> absent | **gap** |
| reaction (tooltip) | `reaction[0].manifestation[0].text` | present. On Synthea rows it holds `Severe`/`Mild`: the `lists.reaction` list-id collision noted in memory, faithfully copied by FHIR |

### Condition (`?patient=&category=problem-list-item`)

| PHP | FHIR | Verified |
|---|---|---|
| title | `code.text` (Condition uses `text`, not `coding.display`) | match |
| active filter | `clinicalStatus` in `active`, `recurrence`. FHIR: `inactive` if `enddate` is past; `resolved` if outcome 1 **or occurrence 1**; `recurrence` if occurrence > 1 | pid 20: 2 = 2, pid 16: 7 = 7, pid 1: 2 = 2, same items |
| order | `onsetDateTime` ascending (= `begdate`) | same order, all 3 |

### MedicationRequest (`?patient=`): how OpenEMR separates the two cards

OpenEMR emits **one MedicationRequest per `lists` medication row and one per
`prescriptions` row** (`PrescriptionService.php:156, 212`), so a Synthea drug
appears twice. The discriminator is `intent`:

| Card | FHIR | Name field | Status rule |
|---|---|---|---|
| Medications (`lists`) | **`intent = plan`** | `medicationCodeableConcept.text` | `active` iff `enddate` null and `activity = 1`; `completed` if an end date; else `stopped` |
| Prescriptions (`prescriptions`) | **`intent = order`** (or `request_intent` when set) | `medicationCodeableConcept.coding[].display` | `completed` if `end_date` and `active = 1`; `active` if `active = 1`; else `stopped` |

Verified on pid 20: 7 `plan` + 7 `order`. `plan` + `status=active` = the 2
PHP medications. `order` with `status` in `active`, `completed` = the 7 PHP
prescriptions (PHP shows `active > 0`, which includes ended scripts).

| PHP Prescriptions column | FHIR | Verified |
|---|---|---|
| Drug | `medicationCodeableConcept.coding[].display` | match |
| Details (`1.00`) | `dosageInstruction[].text` is set **only when `dosage` is non-numeric** (`FhirMedicationRequestService.php:265`); `doseAndRate` only from `size`. Synthea's `1.00` is dropped | **gap** |
| Qty | `dispenseRequest.quantity` | match (blank) |
| Refills | `dispenseRequest.numberOfRepeatsAllowed` | match (0) |
| Filled | `authoredOn` (= `date_added`) | match to the second; FHIR carries `+00:00` |
| order | no `date_modified` in FHIR; sort by `authoredOn` desc | **partial** |

Medications card, second field `drug_dosage_instructions` ->
`dosageInstruction[].text`. Empty for every local row, so not observed.

### CareTeam (`?patient=`)

Built from the same `care_teams` / `care_team_member` tables
(`CareTeamService.php:185-285`): `name` = team name, `status`, participants
with `role`, `member` (Practitioner / RelatedPerson / Organization for the
facility), `period.start` = provider since. **Bundle total 0 for all three
patients**, consistent with the empty tables. Whether each column survives
the mapping cannot be verified until a team exists. Proposal: add one team
to a local patient through OpenEMR's own Care Team edit form (normal,
audited data entry, not a backend change), then compare.

### Observation (`?patient=&category=laboratory`)

| Lab field | FHIR | Verified |
|---|---|---|
| analyte | `code.coding[0]` (LOINC code + display) | present |
| value, unit | `valueQuantity.value`, `.unit`; sometimes `valueString`; sometimes neither (pid 20 has one PHQ result with no value) | present |
| reference range | `referenceRange[0].low/high`, **only when `procedure_result.range` parses as two non-zero numbers split on `-`** (`FhirObservationLaboratoryService.php:266`). `0-5`, `<200`, `>=60` and text ranges produce nothing, and `referenceRange.text` is never set | **gap (partial)** |
| flag | `interpretation[0]` from `procedure_result.abnormal` | present in code; every Synthea row is blank (F-19) |
| date | `effectiveDateTime` | present |
| D54 provenance | `note[0].text` = the `w2x-` extraction id | per D54; to be re-verified with a fresh upload in Phase 2 |
| count | pid 20: FHIR 22 = `procedure_result` 22 (the 211 orders are mostly non-lab, F-42). No paging link was returned | match |

Synthea's "laboratory" results include PHQ-2/PHQ-9/GAD-7 questionnaire
scores, because they were imported as procedure results.

---

## 3. Parity gaps the FHIR API forces

Each of these goes in the migration doc. None will be faked.

| # | Element | What PHP shows | What FHIR gives | Port behaviour |
|---|---|---|---|---|
| G1 | Allergy severity | severity title, e.g. `Severe`, `Moderate`, or the raw code | `criticality` `low` / `high` / `unable-to-assess`. Three severities fold into `low`, four into `high`; an unknown code gives nothing | show `criticality` (Low risk / High risk); highlight `high`. `moderate_to_severe` is highlighted in the port and not in PHP |
| G2 | Patient active status | nothing (only the deceased banner) | `active` always `true` | show `deceased` (banner, as PHP does); do not render `active` as if it meant something; say so |
| G3 | Prescription Details | `1.00` (numeric `dosage`), `size`+unit | numeric dosage dropped; `size` only as `doseAndRate` | render what FHIR has (dose quantity, route, timing, text); blank when absent |
| G4 | Prescription order | `date_modified DESC` | no modified date | `authoredOn` desc |
| G5 | Reference range | `range` string as entered | only a two-number `low-high` with non-zero bounds; no text | show low-high when present, else blank. Affects D54 rows whose range is one-sided |
| G6 | Condition status edge | active if outcome != 1 | `resolved` when **occurrence = 1** ("first") even if outcome is not resolved | the port follows FHIR; a first-occurrence problem drops off. OpenEMR defect, reported, not patched (backend is out of bounds) |
| G7 | Allergy/med status edge | active when `enddate` is in the future | `inactive` / `completed` as soon as any end date exists | the port follows FHIR; a future-dated stop disappears early |
| G8 | Allergy outcome-resolved without end date | hidden | `active` | the port shows it |
| G9 | "No Known Allergies" vs "Nothing Recorded" | depends on `lists_touch` | no equivalent | the port shows one empty text; which wording is decided in Phase 2 and recorded in the matrix |
| G10 | Care Team | full team | mapping unverified (no data) | verify after one team is entered through the UI |
| G11 | Edit / add actions | pencils open PHP editors | read scopes only | links out to the PHP editors, or omitted. Read-only port |
| G12 | Photo | patient photo document | not in `Patient.photo` | default silhouette |
| G13 | Card collapse state | per-user setting (`getUserSetting`) | none | per-viewer `localStorage`, default open |
| G14 | ACL-driven card hiding | cards hidden by ACL and `hiddenCards` | scopes only (`user/X.read`) | a card whose scope is refused shows an error state instead of disappearing |

---

## 4. Findings that change the plan

### 4.1 A public client cannot hold `user/` scopes on this OpenEMR

The work order specifies a SMART **public** client with authorization code +
PKCE and user read scopes. OpenEMR refuses that combination at registration:

```
$ curl -sk -X POST https://localhost:9300/oauth2/default/registration \
    -H 'Content-Type: application/json' \
    --data '{"application_type":"public", ..., "token_endpoint_auth_method":"none",
             "scope":"openid fhirUser user/Patient.read"}'
{"error":"invalid_client_metadata","error_description":"system and user scopes are
only allowed for confidential clients", ...}
```

Source: `src/RestControllers/AuthorizationController.php:320-326`. The admin
UI says the same: "system, user, and offline_access scopes require
confidential app permissions" (`interface/smart/register-app.php:449`). A
public client is registered with `client_role = patient` and can only hold
`patient/` scopes. That means a patient portal login, not a clinician
login. Changing it means changing the backend, which the requirement
forbids.

### 4.2 OpenEMR redeems a confidential client's code with no secret, and with no PKCE

`ClientRepository::validateClient()` checks the secret only when one is sent
(`src/Common/Auth/OpenIDConnect/Repositories/ClientRepository.php:198`, `if
(!empty($clientSecret) && ...)`). It returns `true` otherwise. Checked live
on the local stack with a confidential client, a physician login and consent:

```
token status (no secret, with PKCE): 200  [id_token, scope, token_type, expires_in, access_token]
token status (no secret, NO PKCE):   200  [id_token, scope, token_type, expires_in, access_token]
```

So for the authorization-code grant, **a confidential client's secret
protects nothing at the token endpoint.** Anyone who obtains a code, for
example from a leaked redirect, can redeem it, and nothing requires PKCE.
This affects the Week 1 chart-embed client too, which is confidential and
authorization-code only. It is a security finding for `docs/audit/findings.md`
(proposed next F-number). It is recorded here and not fixed: the backend is
out of bounds for this work order.

It also means a SPA *can* run authorization code + PKCE against a
confidential-registered client, holding no secret. That works only because
of the defect above, so it is not a sound basis for the design.

---

## 5. Stack notes for Phase 2

- **Styling:** OpenEMR's theme `public/themes/style_light.css` is **Bootstrap
  4.6.2** compiled with OpenEMR's overrides; icons are **Font Awesome Free
  6.7.2** (`package.json`). The port uses the same two npm packages at the
  same versions, bundled locally (no CDN), plus OpenEMR's theme variables.
  No Tailwind.
- The inventory client `6o92KC0B…` ("dashboard-port inventory (local,
  WO31)") was registered on the local stack by this session and enabled by
  setting `is_enabled=1` in SQL. Locally `admin/pass` is dead, and that SQL
  is how prior local sessions did the admin approval step. It is a local,
  out-of-band edit (F-40 class). Its secret lives only in the session
  scratchpad.
