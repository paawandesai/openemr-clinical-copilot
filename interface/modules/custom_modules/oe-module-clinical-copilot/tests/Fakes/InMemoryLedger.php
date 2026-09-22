<?php

/**
 * An in-memory Ledger, including the UNIQUE-key race the real one relies on.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Common\Database\SqlQueryException;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackResult;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LedgerEntry;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\ProcedureChainIds;

/**
 * `raceWinner` models the one thing the real ledger's UNIQUE constraint is
 * for: another request inserting the same key between our lookup and our
 * insert. When set, the first `record()` throws the way MariaDB's duplicate
 * key does, and the rows the winner wrote appear on the next lookup — which
 * is precisely the sequence the service's rollback path has to survive.
 */
final class InMemoryLedger implements \OpenEMR\Modules\ClinicalCopilot\WriteBack\Ledger
{
    /** @var array<string, LedgerEntry> */
    public array $rows = [];

    public int $lookups = 0;

    /** @var array<string, LedgerEntry> rows another writer commits during our transaction */
    private array $raceWinner = [];

    private bool $raceFired = false;

    /** @param array<string, LedgerEntry> $winner */
    public function withRaceWinner(array $winner): self
    {
        $this->raceWinner = $winner;
        return $this;
    }

    public function findByIdempotencyKey(string $idempotencyKey): ?LedgerEntry
    {
        $this->lookups++;
        return $this->rows[$idempotencyKey] ?? null;
    }

    public function record(
        LabWriteBackRequest $request,
        LabWriteBackResult $result,
        int $documentId,
        ProcedureChainIds $ids
    ): int {
        if ($this->raceWinner !== [] && !$this->raceFired) {
            $this->raceFired = true;
            // The winner's rows are now visible to the next reader, which is
            // what the service does after its own transaction rolls back.
            $this->rows = $this->raceWinner;
            throw new SqlQueryException(
                'INSERT INTO `module_clinical_copilot_writeback_ledger`',
                'Duplicate entry for key idempotency_key',
                1062,
            );
        }
        $this->rows[$result->idempotencyKey] = new LedgerEntry(
            $result->idempotencyKey,
            $result->extractionId,
            $documentId,
            $ids->procedureOrderId,
            $ids->procedureReportId,
            $ids->procedureResultId,
        );
        return count($this->rows);
    }
}
