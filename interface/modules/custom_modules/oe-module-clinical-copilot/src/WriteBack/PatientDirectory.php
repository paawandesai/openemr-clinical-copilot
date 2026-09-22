<?php

/**
 * Resolves a FHIR Patient uuid to the pid the procedure tables use.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Uuid\UuidRegistry;

/**
 * Callers name a patient by uuid and never by pid. OpenEMR assigns `pid`
 * and `pubpid` per instance at import time, so the same person has
 * different numbers on the laptop and on the deployment while the uuid is
 * the one identifier that carries across (docs/w2/LOCAL_ENV.md §1). An
 * endpoint that accepted a pid would be an endpoint that writes a lab
 * result into whichever chart happens to hold that number.
 */
final readonly class PatientDirectory implements PatientLookup
{
    /**
     * @throws WriteBackFailed if no such patient exists on this instance.
     */
    public function requirePid(string $patientUuid): int
    {
        $row = QueryUtils::querySingleRow(
            'SELECT `pid` FROM `patient_data` WHERE `uuid` = ?',
            [UuidRegistry::uuidToBytes($patientUuid)]
        );
        if (!is_array($row) || !isset($row['pid'])) {
            throw new WriteBackFailed('no patient with that uuid exists here', 404);
        }
        return Row::int($row, 'pid');
    }
}
