<?php

/**
 * The outcome of quarantining an upload, and whether this call changed the chart.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

final readonly class QuarantineResult
{
    public function __construct(
        public QuarantineOutcome $outcome,
        public ?int $documentId = null,
        /** True only for the call that moved it: that call writes the audit entry. */
        public bool $moved = false,
    ) {
    }
}
