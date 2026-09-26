<?php

/**
 * A WriteBackAudit that records what it was told instead of writing a log row.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\WriteBack\WriteBackAudit;

final class RecordingAudit implements WriteBackAudit
{
    /** @var list<array{pid: int, order: int, results: list<int>}> */
    public array $written = [];

    /** @var list<array{pid: int, reason: string}> */
    public array $abandoned = [];

    /** @var list<array{pid: int, chart_results: list<int>}> */
    public array $conflicts = [];

    /** @param list<int> $resultIds */
    public function orderWritten(int $pid, int $procedureOrderId, array $resultIds): void
    {
        $this->written[] = ['pid' => $pid, 'order' => $procedureOrderId, 'results' => $resultIds];
    }

    public function writeAbandoned(int $pid, string $reason): void
    {
        $this->abandoned[] = ['pid' => $pid, 'reason' => $reason];
    }

    /** @param list<int> $chartResultIds */
    public function conflictsFound(int $pid, array $chartResultIds): void
    {
        $this->conflicts[] = ['pid' => $pid, 'chart_results' => $chartResultIds];
    }
}
