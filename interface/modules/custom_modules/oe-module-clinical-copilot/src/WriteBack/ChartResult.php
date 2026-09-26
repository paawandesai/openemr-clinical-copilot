<?php

/**
 * A lab result the chart already holds, from any source.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use DateTimeImmutable;

/**
 * One `procedure_result` row with the ids above it and the two columns the
 * duplicate check compares: `result` and `units`, as stored. Whether this
 * endpoint wrote it, a lab interface filed it or a clinician typed it is
 * deliberately not carried: a result is a result, whoever entered it.
 */
final readonly class ChartResult
{
    public function __construct(
        public int $procedureOrderId,
        public int $procedureReportId,
        public int $procedureResultId,
        public string $result,
        public string $units,
        public ?DateTimeImmutable $dateCollected,
    ) {
    }

    public function ids(): ProcedureChainIds
    {
        return new ProcedureChainIds($this->procedureOrderId, $this->procedureReportId, $this->procedureResultId);
    }
}
