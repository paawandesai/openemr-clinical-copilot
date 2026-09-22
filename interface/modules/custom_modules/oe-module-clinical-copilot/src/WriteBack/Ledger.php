<?php

/**
 * The idempotency ledger.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

interface Ledger
{
    public function findByIdempotencyKey(string $idempotencyKey): ?LedgerEntry;

    /**
     * Records one written result. A duplicate key here is a race, not a bug:
     * the caller rolls back and re-reads.
     *
     * @throws \OpenEMR\Common\Database\SqlQueryException on a duplicate key
     */
    public function record(
        LabWriteBackRequest $request,
        LabWriteBackResult $result,
        int $documentId,
        ProcedureChainIds $ids
    ): int;
}
