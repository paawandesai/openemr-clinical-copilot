<?php

/**
 * A Pre-Visit Brief could not be produced for a request.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final class BriefUnavailable extends \RuntimeException
{
    /**
     * @param ?string $correlationId The agent's correlation id, when the agent answered with one.
     */
    public function __construct(
        public readonly BriefFailure $failure,
        public readonly ?string $correlationId = null,
        ?\Throwable $previous = null,
    ) {
        parent::__construct('Pre-Visit Brief unavailable', 0, $previous);
    }
}
