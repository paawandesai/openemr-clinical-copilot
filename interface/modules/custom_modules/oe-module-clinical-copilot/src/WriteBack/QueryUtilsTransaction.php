<?php

/**
 * The production Transaction: OpenEMR's own ADODB transaction.
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

final readonly class QueryUtilsTransaction implements Transaction
{
    /**
     * @phpstan-template T
     * @param callable(): T $work
     * @return T
     */
    public function run(callable $work): mixed
    {
        return QueryUtils::inTransaction($work);
    }
}
