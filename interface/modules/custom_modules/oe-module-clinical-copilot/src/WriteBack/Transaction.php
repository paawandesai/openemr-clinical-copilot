<?php

/**
 * Runs a unit of work inside a database transaction.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

/**
 * A seam, and the reason for it: the write-back service's whole contract is
 * "all of it or none of it", and the only way to test that claim is to make
 * the transaction something a test can fail on purpose. `QueryUtils` is
 * static, so without this the rollback path could only ever be exercised
 * against a live database — which is where it is *also* exercised, but not
 * where it can be exercised cheaply.
 */
interface Transaction
{
    /**
     * Runs $work, committing on return and rolling back on any throwable,
     * which is then re-thrown.
     *
     * @phpstan-template T
     * @param callable(): T $work
     * @return T
     */
    public function run(callable $work): mixed;
}
