<?php

/**
 * Resolves a FHIR Patient uuid to a pid.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

interface PatientLookup
{
    /**
     * @throws WriteBackFailed if no such patient exists on this instance.
     */
    public function requirePid(string $patientUuid): int;
}
