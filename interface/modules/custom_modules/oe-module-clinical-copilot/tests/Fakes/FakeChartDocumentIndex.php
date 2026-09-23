<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\W2\ChartDocumentIndex;

/**
 * The index, answering from constructor arguments and recording the lock.
 */
final class FakeChartDocumentIndex implements ChartDocumentIndex
{
    /** @var list<string> */
    public array $lockEvents = [];

    public function __construct(
        private ?int $existing = null,
        private bool $elsewhere = false,
        private bool $lockable = true,
    ) {
    }

    public function lock(string $name): bool
    {
        if ($this->lockable) {
            $this->lockEvents[] = 'lock';
        }
        return $this->lockable;
    }

    public function unlock(string $name): void
    {
        $this->lockEvents[] = 'unlock';
    }

    public function findForPatient(int $pid, string $sha3512): ?int
    {
        return $this->existing;
    }

    public function isFiledOnAnotherPatient(int $pid, string $sha3512, array $categoryIds): bool
    {
        return $this->elsewhere;
    }
}
