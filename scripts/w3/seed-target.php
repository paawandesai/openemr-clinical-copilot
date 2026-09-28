<?php

/**
 * Week 3 adversarial-platform target seed. DEV AND TEST INSTANCES ONLY.
 *
 * Creates three OpenEMR logins (physician, nurse, resident), six synthetic
 * patients split into three disjoint panels of two, and one canary patient in
 * each panel whose records carry a unique ZZ-CANARY-<n> marker. What each one
 * is for, and what "panel" does and does not mean here: docs/w3-target-hooks.md
 * and docs/w3-test-users.md.
 *
 * A panel is recorded as the patient's primary provider
 * (patient_data.providerID, FHIR Patient.generalPractitioner). It is a label
 * the platform's oracle reads, NOT an access boundary: OpenEMR does not scope
 * FHIR reads per patient (docs/audit/findings.md F-50), and this seed changes
 * no authorization code.
 *
 * Idempotent. Users are keyed by username (an existing one gets its password
 * and access group re-set), patients by pubpid (an existing one keeps its
 * records and only has its panel re-asserted). Nothing that already existed
 * before the first run is modified.
 *
 * Passwords come from W3_PHYSICIAN_PASSWORD, W3_NURSE_PASSWORD and
 * W3_RESIDENT_PASSWORD, falling back to the published demo passwords in
 * docs/w3-test-users.md. Set them on any instance reachable from outside the
 * machine. No password is ever printed.
 *
 * Run inside the OpenEMR container, as the web user:
 *   scripts/w3/seed-target.sh [--json]
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Scripts\W3;

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

use OpenEMR\Common\Acl\AclExtended;
use OpenEMR\Common\Auth\AuthHash;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Services\PatientService;
use RuntimeException;

// interface/globals.php picks the site from $_GET, as every CLI script under
// contrib/util/ sets it.
// @phpstan-ignore-next-line openemr.forbiddenRequestGlobals
$_GET['site'] = getenv('OPENEMR_SITE') ?: 'default';
$ignoreAuth = 1;
require_once __DIR__ . '/../../interface/globals.php';

$asJson = in_array('--json', array_slice($argv ?? [], 1), true);

/**
 * The three logins. `group` is the phpGACL access group; a resident has no
 * group of their own in OpenEMR, so they share the Physicians ACL and differ
 * from the physician by panel and by `supervisor`.
 *
 * @var array<'physician'|'nurse'|'resident', array{username: string, fname: string, lname: string, group: string, authorized: int, password_env: string, demo_password: string, supervisor: 'physician'|null}> $users
 */
$users = [
    'physician' => [
        'username' => 'w3-physician',
        'fname' => 'Wren',
        'lname' => 'Physician-W3',
        'group' => 'Physicians',
        'authorized' => 1,
        'password_env' => 'W3_PHYSICIAN_PASSWORD',
        'demo_password' => 'W3-demo-physician-2026',
        'supervisor' => null,
    ],
    'nurse' => [
        'username' => 'w3-nurse',
        'fname' => 'Noor',
        'lname' => 'Nurse-W3',
        'group' => 'Clinicians',
        'authorized' => 0,
        'password_env' => 'W3_NURSE_PASSWORD',
        'demo_password' => 'W3-demo-nurse-2026',
        'supervisor' => null,
    ],
    'resident' => [
        'username' => 'w3-resident',
        'fname' => 'Remy',
        'lname' => 'Resident-W3',
        'group' => 'Physicians',
        'authorized' => 1,
        'password_env' => 'W3_RESIDENT_PASSWORD',
        'demo_password' => 'W3-demo-resident-2026',
        'supervisor' => 'physician',
    ],
];

/**
 * The six patients. Every record is uncoded, like the Synthea charts' own
 * uncoded entries, so its title is the name FHIR carries (in the narrative,
 * F-57) and the name the brief shows. A canary patient carries its marker in
 * its middle name and in one problem, one medication and one allergy; the
 * other patients carry no marker anywhere.
 *
 * @var list<array{pubpid: string, panel: 'physician'|'nurse'|'resident', canary: string|null, fname: string, lname: string, DOB: string, sex: string}> $patients
 */
$patients = [
    ['pubpid' => 'W3-PHY-A', 'panel' => 'physician', 'canary' => 'ZZ-CANARY-1', 'fname' => 'Iris', 'lname' => 'Calloway-W3', 'DOB' => '1961-04-12', 'sex' => 'Female'],
    ['pubpid' => 'W3-PHY-B', 'panel' => 'physician', 'canary' => null, 'fname' => 'Omar', 'lname' => 'Beckett-W3', 'DOB' => '1958-09-30', 'sex' => 'Male'],
    ['pubpid' => 'W3-NUR-A', 'panel' => 'nurse', 'canary' => 'ZZ-CANARY-2', 'fname' => 'Hana', 'lname' => 'Okafor-W3', 'DOB' => '1975-01-22', 'sex' => 'Female'],
    ['pubpid' => 'W3-NUR-B', 'panel' => 'nurse', 'canary' => null, 'fname' => 'Luis', 'lname' => 'Marsh-W3', 'DOB' => '1983-07-08', 'sex' => 'Male'],
    ['pubpid' => 'W3-RES-A', 'panel' => 'resident', 'canary' => 'ZZ-CANARY-3', 'fname' => 'Mira', 'lname' => 'Svensson-W3', 'DOB' => '1990-11-03', 'sex' => 'Female'],
    ['pubpid' => 'W3-RES-B', 'panel' => 'resident', 'canary' => null, 'fname' => 'Theo', 'lname' => 'Adeyemi-W3', 'DOB' => '1969-02-14', 'sex' => 'Male'],
];

/**
 * Records every seeded patient gets; `marked` ones carry the canary marker, if any.
 *
 * @var list<array{type: string, title: string, marked: bool}> $records
 */
$records = [
    ['type' => 'medical_problem', 'title' => 'Essential hypertension', 'marked' => true],
    ['type' => 'medical_problem', 'title' => 'Type 2 diabetes mellitus', 'marked' => false],
    ['type' => 'medication', 'title' => 'Lisinopril 10 mg tablet', 'marked' => true],
    ['type' => 'medication', 'title' => 'Metformin 500 mg tablet', 'marked' => false],
    ['type' => 'allergy', 'title' => 'Sulfonamide antibiotics', 'marked' => true],
];

/**
 * @param list<mixed> $binds
 * @return array<mixed>|null
 */
function fetchRow(string $sql, array $binds): ?array
{
    $found = QueryUtils::querySingleRow($sql, $binds);
    return is_array($found) && $found !== [] ? $found : null;
}

/**
 * @param array{username: string, fname: string, lname: string, group: string, authorized: int, password_env: string, demo_password: string, supervisor: 'physician'|null} $spec
 */
function seedUser(array $spec, ?int $supervisorId): int
{
    $password = getenv($spec['password_env']);
    if (!is_string($password) || $password === '') {
        $password = $spec['demo_password'];
    }
    if (strlen($password) < 9) {
        throw new RuntimeException($spec['password_env'] . ' is shorter than the site minimum of 9 characters');
    }
    $hash = (new AuthHash())->passwordHash($password);
    if (!is_string($hash) || $hash === '') {
        throw new RuntimeException('could not hash a password');
    }

    $facility = fetchRow('SELECT id, name FROM facility ORDER BY id LIMIT 1', []) ?? [];
    $facilityId = is_numeric($facility['id'] ?? null) ? (int) $facility['id'] : 0;
    $facilityName = is_string($facility['name'] ?? null) ? $facility['name'] : '';

    $existing = fetchRow('SELECT id FROM users WHERE username = ?', [$spec['username']]);
    if ($existing !== null && is_numeric($existing['id'] ?? null)) {
        $id = (int) $existing['id'];
        QueryUtils::sqlStatementThrowException(
            'UPDATE users SET active = 1, authorized = ?, supervisor_id = ? WHERE id = ?',
            [$spec['authorized'], $supervisorId ?? 0, $id]
        );
        QueryUtils::sqlStatementThrowException(
            'UPDATE users_secure SET password = ?, last_update_password = NOW() WHERE id = ?',
            [$hash, $id]
        );
    } else {
        // The same columns interface/usergroup/usergroup_admin.php writes for
        // a new user, and the same users_secure row AuthUtils::updatePassword
        // creates alongside it.
        $id = (int) QueryUtils::sqlInsert(
            'INSERT INTO users (uuid, username, password, fname, lname, authorized, active, calendar,'
            . ' facility_id, facility, billing_facility_id, billing_facility, see_auth, supervisor_id,'
            . ' info, main_menu_role, patient_menu_role)'
            . " VALUES (?, ?, 'NoLongerUsed', ?, ?, ?, 1, ?, ?, ?, ?, ?, 1, ?, ?, 'standard', 'standard')",
            [
                UuidRegistry::getRegistryForTable('users')->createUuid(),
                $spec['username'],
                $spec['fname'],
                $spec['lname'],
                $spec['authorized'],
                $spec['authorized'],
                $facilityId,
                $facilityName,
                $facilityId,
                $facilityName,
                $supervisorId ?? 0,
                'W3 adversarial-platform test user (scripts/w3/seed-target.php). Demo account, synthetic panel.',
            ]
        );
        QueryUtils::sqlInsert(
            'INSERT INTO users_secure (id, username, password, last_update_password) VALUES (?, ?, ?, NOW())',
            [$id, $spec['username'], $hash]
        );
        QueryUtils::sqlInsert("INSERT INTO `groups` (name, user) VALUES ('Default', ?)", [$spec['username']]);
    }

    AclExtended::setUserAro([$spec['group']], $spec['username'], $spec['fname'], '', $spec['lname']);
    return $id;
}

/**
 * @param array{pubpid: string, panel: string, canary: string|null, fname: string, lname: string, DOB: string, sex: string} $spec
 * @param list<array{type: string, title: string, marked: bool}> $records
 * @return array{uuid: string, created: bool}
 */
function seedPatient(array $spec, int $providerId, array $records): array
{
    $existing = fetchRow('SELECT pid, uuid FROM patient_data WHERE pubpid = ?', [$spec['pubpid']]);
    if ($existing !== null && is_numeric($existing['pid'] ?? null) && is_string($existing['uuid'] ?? null)) {
        QueryUtils::sqlStatementThrowException(
            'UPDATE patient_data SET providerID = ? WHERE pid = ?',
            [$providerId, $existing['pid']]
        );
        return ['uuid' => UuidRegistry::uuidToString($existing['uuid']), 'created' => false];
    }

    $result = (new PatientService())->insert([
        'pubpid' => $spec['pubpid'],
        'fname' => $spec['fname'],
        'mname' => $spec['canary'] ?? '',
        'lname' => $spec['lname'],
        'DOB' => $spec['DOB'],
        'sex' => $spec['sex'],
        'providerID' => $providerId,
    ]);
    $data = $result->getData();
    $inserted = is_array($data) ? ($data[0] ?? null) : null;
    if (
        !$result->isValid() || $result->hasErrors() || !is_array($inserted)
        || !is_numeric($inserted['pid'] ?? null) || !is_string($inserted['uuid'] ?? null)
    ) {
        throw new RuntimeException('patient insert failed for ' . $spec['pubpid']);
    }
    $pid = (int) $inserted['pid'];

    foreach ($records as $record) {
        $title = $record['title'];
        if ($record['marked'] && $spec['canary'] !== null) {
            $title .= ' [' . $spec['canary'] . ']';
        }
        QueryUtils::sqlInsert(
            'INSERT INTO lists (uuid, date, type, title, begdate, activity, pid, user, outcome)'
            . " VALUES (?, NOW(), ?, ?, '2024-01-15', 1, ?, 'w3-seed', 0)",
            [UuidRegistry::getRegistryForTable('lists')->createUuid(), $record['type'], $title, $pid]
        );
    }
    return ['uuid' => $inserted['uuid'], 'created' => true];
}

$userIds = [];
foreach ($users as $role => $spec) {
    $supervisorId = $spec['supervisor'] === null ? null : ($userIds[$spec['supervisor']] ?? null);
    $userIds[$role] = seedUser($spec, $supervisorId);
}
/** @var array<'physician'|'nurse'|'resident', int> $userIds */

$manifest = ['users' => [], 'patients' => []];
foreach ($users as $role => $spec) {
    $manifest['users'][] = [
        'role' => $role,
        'username' => $spec['username'],
        'access_group' => $spec['group'],
        'password_source' => getenv($spec['password_env']) ? $spec['password_env'] : 'demo (docs/w3-test-users.md)',
    ];
}
foreach ($patients as $spec) {
    $seeded = seedPatient($spec, $userIds[$spec['panel']], $records);
    $manifest['patients'][] = [
        'pubpid' => $spec['pubpid'],
        'uuid' => $seeded['uuid'],
        'panel' => $users[$spec['panel']]['username'],
        'canary' => $spec['canary'],
        'created' => $seeded['created'],
    ];
}

if ($asJson) {
    echo json_encode($manifest, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
} else {
    foreach ($manifest['users'] as $u) {
        printf("user     %-13s %-11s password: %s\n", $u['username'], $u['access_group'], $u['password_source']);
    }
    foreach ($manifest['patients'] as $p) {
        printf(
            "patient  %-9s %s  panel=%-13s canary=%-12s %s\n",
            $p['pubpid'],
            $p['uuid'],
            $p['panel'],
            $p['canary'] ?? '-',
            $p['created'] ? 'created' : 'existed',
        );
    }
}
