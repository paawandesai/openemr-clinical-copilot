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
 * (agent/app/models/w2/writeback.py), so `toArray()` is the wire format and
 * the only place field names are spelled.
 *
 * `procedureOrderId` is one value for a response whose results may span
 * several writes: it is the order this call created, and — when this call
 * created nothing — the order recorded for the first result that has one
 * (an earlier write of this file, or the chart row it matched). Null when
 * no result has an order: every result a conflict. Per-result order ids
 * are on the ledger; the response's job is to name the requisition this
 * request produced.
 */
final readonly class LabWriteBackOutcome
{
    /** @param non-empty-list<ResultOutcome> $results */
    public function __construct(
        public string $patientUuid,
        public int $documentId,
        public WriteOutcome $documentOutcome,
        public ?int $procedureOrderId,
        public array $results,
        public string $correlationId,
    ) {
    }

    /** @return array<string, mixed> */
    public function toArray(): array
    {
        return [
            'patient_uuid' => $this->patientUuid,
            'document_id' => $this->documentId,
            'document_outcome' => $this->documentOutcome->value,
            'procedure_order_id' => $this->procedureOrderId,
            'results' => array_map(static fn(ResultOutcome $r): array => $r->toArray(), $this->results),
            'correlation_id' => $this->correlationId,
            'counts' => $this->counts(),
        ];
    }

    /** How many results this call actually wrote. */
    public function createdCount(): int
    {
        return $this->count(WriteOutcome::CREATED);
    }

    /**
     * One count per outcome, in the enum's order.
     *
     * @return array<string, int>
     */
    public function counts(): array
    {
        $counts = [];
        foreach (WriteOutcome::cases() as $outcome) {
            $counts[$outcome->value] = $this->count($outcome);
        }
        return $counts;
    }

    /**
     * `procedure_result` ids of the chart rows this document disagrees with.
     *
     * @return list<int>
     */
    public function conflictingChartResultIds(): array
    {
        $ids = [];
        foreach ($this->results as $result) {
            if ($result->outcome === WriteOutcome::CONFLICT && $result->matchedProcedureResultId !== null) {
                $ids[] = $result->matchedProcedureResultId;
            }
        }
        return $ids;
    }

    private function count(WriteOutcome $outcome): int
    {
        return count(array_filter(
            $this->results,
            static fn(ResultOutcome $r): bool => $r->outcome === $outcome
        ));
    }
}
