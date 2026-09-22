<?php

/**
 * What the write-back endpoint did, and the ids it is answering with.
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
 * Shaped to the agent-side `LabWriteBackResponse`
 * (agent/app/models/w2/writeback.py), so `toArray()` is the wire format
 * and the only place field names are spelled.
 */
final readonly class LabWriteBackOutcome
{
    public function __construct(
        public string $patientUuid,
        public int $documentId,
        public WriteOutcome $documentOutcome,
        public string $testCode,
        public string $idempotencyKey,
        public string $extractionId,
        public WriteOutcome $resultOutcome,
        public ProcedureChainIds $ids,
        public string $correlationId,
    ) {
    }

    /**
     * The ids a previous identical request already recorded. Nothing is
     * written, and the answer is the same one that request received.
     */
    public static function replayed(LabWriteBackRequest $request, LedgerEntry $entry): self
    {
        return new self(
            $request->patientUuid,
            $entry->documentId,
            WriteOutcome::DUPLICATE_IGNORED,
            $request->onlyResult()->testCode,
            $entry->idempotencyKey,
            $entry->extractionId,
            WriteOutcome::DUPLICATE_IGNORED,
            new ProcedureChainIds(
                $entry->procedureOrderId,
                $entry->procedureReportId,
                $entry->procedureResultId
            ),
            $request->correlationId,
        );
    }

    /** @return array<string, mixed> */
    public function toArray(): array
    {
        return [
            'patient_uuid' => $this->patientUuid,
            'document_id' => $this->documentId,
            'document_outcome' => $this->documentOutcome->value,
            'procedure_order_id' => $this->ids->procedureOrderId,
            'results' => [
                [
                    'test_code' => $this->testCode,
                    'idempotency_key' => $this->idempotencyKey,
                    'extraction_id' => $this->extractionId,
                    'outcome' => $this->resultOutcome->value,
                    'procedure_report_id' => $this->ids->procedureReportId,
                    'procedure_result_id' => $this->ids->procedureResultId,
                ],
            ],
            'correlation_id' => $this->correlationId,
        ];
    }
}
