<?php

/**
 * One row of the write-back ledger, as read back.
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
 * The ids a previous write recorded, which is all a retry needs: returning
 * these unchanged is what makes the second identical request create zero
 * rows and still answer with real ids (CLAUDE.md invariant 6).
 */
final readonly class LedgerEntry
{
    public function __construct(
        public string $idempotencyKey,
        public string $extractionId,
        public int $documentId,
        public int $procedureOrderId,
        public int $procedureReportId,
        public int $procedureResultId,
    ) {
    }
}
