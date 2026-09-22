<?php

/**
 * What the endpoint did with one result.
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
 * Shaped to the agent-side `LabWriteBackResultOutcome`. Per result rather
 * than per request, because idempotency is per result: one request can
 * create some rows and recognise others as already written.
 */
final readonly class ResultOutcome
{
    public function __construct(
        public string $testCode,
        public string $idempotencyKey,
        public string $extractionId,
        public WriteOutcome $outcome,
        public ProcedureChainIds $ids,
    ) {
    }

    public static function created(LabWriteBackResult $result, ProcedureChainIds $ids): self
    {
        return new self(
            $result->testCode,
            $result->idempotencyKey,
            $result->extractionId,
            WriteOutcome::CREATED,
            $ids,
        );
    }

    public static function alreadyWritten(LabWriteBackResult $result, LedgerEntry $entry): self
    {
        return new self(
            $result->testCode,
            $entry->idempotencyKey,
            $entry->extractionId,
            WriteOutcome::DUPLICATE_IGNORED,
            new ProcedureChainIds(
                $entry->procedureOrderId,
                $entry->procedureReportId,
                $entry->procedureResultId
            ),
        );
    }

    /** @return array<string, mixed> */
    public function toArray(): array
    {
        return [
            'test_code' => $this->testCode,
            'idempotency_key' => $this->idempotencyKey,
            'extraction_id' => $this->extractionId,
            'outcome' => $this->outcome->value,
            'procedure_report_id' => $this->ids->procedureReportId,
            'procedure_result_id' => $this->ids->procedureResultId,
        ];
    }
}
