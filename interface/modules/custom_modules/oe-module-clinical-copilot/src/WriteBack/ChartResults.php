<?php

/**
 * The chart's own lab results, read before a row is written.
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
 * The ledger (D1) is keyed on the file, so it cannot see the same results
 * arriving in a different file: a re-scan, a re-print, a report faxed
 * twice. This is the check that can. It reads every result the patient
 * has for a test code on a collection date, from any source.
 */
interface ChartResults
{
    /**
     * The patient's results for `$testCode` collected on `$collectionDate`,
     * oldest first. With `$collectedAt` (the report printed a time), only
     * those collected at that exact time or with no time recorded (a
     * midnight timestamp, as a date-only entry is stored); without it, every
     * result of that day.
     *
     * @return list<ChartResult>
     */
    public function findCollected(
        int $pid,
        string $testCode,
        DateTimeImmutable $collectionDate,
        ?DateTimeImmutable $collectedAt
    ): array;
}
