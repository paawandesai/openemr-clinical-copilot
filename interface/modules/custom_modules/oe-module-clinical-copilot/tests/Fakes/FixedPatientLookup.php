<?php

/**
 * A PatientLookup that answers with a fixed pid.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\WriteBack\PatientLookup;

final readonly class FixedPatientLookup implements PatientLookup
{
    public function __construct(private int $pid = 1)
    {
    }

    public function requirePid(string $patientUuid): int
    {
        return $this->pid;
    }
}
