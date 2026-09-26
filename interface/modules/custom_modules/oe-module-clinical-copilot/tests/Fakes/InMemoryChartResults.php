<?php

/**
 * A ChartResults over a list of rows, applying the interface's date rule.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use DateTimeImmutable;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\ChartResult;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\ChartResults;

/**
 * Rows are keyed by pid and test code, as another source (a lab interface,
 * a hand entry, this endpoint under another file) would have left them.
 * The date rule is the real one's, so a test can put a row at a different
 * time or on a different day and see it ignored.
 */
final class InMemoryChartResults implements ChartResults
{
    /** @var list<array{pid: int, code: string, row: ChartResult}> */
    private array $rows = [];

    /** @var list<array{int, string, string, ?string}> every lookup made: pid, code, date, time */
    public array $lookups = [];

    public function with(int $pid, string $testCode, ChartResult $row): self
    {
        $this->rows[] = ['pid' => $pid, 'code' => strtoupper(trim($testCode)), 'row' => $row];
        return $this;
    }

    public function findCollected(
        int $pid,
        string $testCode,
        DateTimeImmutable $collectionDate,
        ?DateTimeImmutable $collectedAt
    ): array {
        $this->lookups[] = [$pid, $testCode, $collectionDate->format('Y-m-d'), $collectedAt?->format('H:i:s')];
        $found = [];
        foreach ($this->rows as $entry) {
            $row = $entry['row'];
            if ($entry['pid'] !== $pid || $entry['code'] !== strtoupper(trim($testCode))) {
                continue;
            }
            if ($row->dateCollected === null || $row->dateCollected->format('Y-m-d') !== $collectionDate->format('Y-m-d')) {
                continue;
            }
            if (
                $collectedAt !== null
                && $row->dateCollected->format('H:i:s') !== '00:00:00'
                && $row->dateCollected->format('H:i:s') !== $collectedAt->format('H:i:s')
            ) {
                continue;
            }
            $found[] = $row;
        }
        usort($found, static fn(ChartResult $a, ChartResult $b): int => $a->procedureResultId <=> $b->procedureResultId);
        return $found;
    }
}
