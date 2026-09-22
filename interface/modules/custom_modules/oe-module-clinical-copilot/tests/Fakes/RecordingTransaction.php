<?php

/**
 * A Transaction that runs the work in-process and records the outcome.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\WriteBack\Transaction;
use Throwable;

/**
 * Mirrors `QueryUtils::inTransaction`: commit on return, roll back and
 * re-throw on anything thrown. There is no database, so "rolled back" is
 * recorded rather than performed — which is exactly what the tests assert
 * about, since the question is whether the service reaches the rollback
 * path and cleans up after itself.
 */
final class RecordingTransaction implements Transaction
{
    public int $committed = 0;
    public int $rolledBack = 0;

    /**
     * @phpstan-template T
     * @param callable(): T $work
     * @return T
     */
    public function run(callable $work): mixed
    {
        try {
            $result = $work();
        } catch (Throwable $exception) {
            $this->rolledBack++;
            throw $exception;
        }
        $this->committed++;
        return $result;
    }
}
