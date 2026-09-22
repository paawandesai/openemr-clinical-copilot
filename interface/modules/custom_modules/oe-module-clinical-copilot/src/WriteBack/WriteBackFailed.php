<?php

/**
 * A write-back request that was well formed but could not be completed.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use RuntimeException;

/**
 * Separate from InvalidWriteBackRequest because the two map to different
 * status codes and, more importantly, to different advice: a 400 means the
 * caller sends a different request, a 409/500 means the caller retries the
 * same one. Retrying is safe by construction — the ledger's UNIQUE key is
 * what makes it so.
 */
final class WriteBackFailed extends RuntimeException
{
    public function __construct(string $reason, public readonly int $statusCode = 500)
    {
        parent::__construct($reason);
    }
}
