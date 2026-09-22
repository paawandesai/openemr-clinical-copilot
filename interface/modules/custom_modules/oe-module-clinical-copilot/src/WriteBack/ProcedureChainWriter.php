<?php

/**
 * Writes the procedure_order -> ... -> procedure_result chain.
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
use OpenEMR\Common\Uuid\UuidRegistry;

/**
 * OpenEMR 8.2 has no API that writes a lab result. Every FHIR write route
 * returns 404, no `Observation` write scope is defined even in principle,
 * and `ProcedureService` — the class behind the FHIR lab read — exposes no
 * insert method at all (docs/w2/spikes/writeback.md §2, §5). So D1 puts
 * the four-table insert inside the fork, where it runs through OpenEMR's
 * own database layer.
 *
 * Going through `QueryUtils` rather than Doctrine directly is what keeps
 * these writes auditable: `ADODB_mysqli_log::Execute()` hands every
 * statement to `EventAuditLogger::auditSQLEvent()`, which maps
 * `procedure_report` and `procedure_result` to the `lab-results` audit
 * event. It does *not* audit `procedure_order` — `WriteBackAudit` covers
 * that, from outside the transaction, and says why.
 *
 * ### One order, N results
 *
 * A panel arrives as one document and several analytes. They share one
 * `procedure_order` — the requisition — and each gets its own
 * `procedure_order_code` / `procedure_report` / `procedure_result` at
 * sequence 1..N, so each analyte keeps its own LOINC code and printed name
 * in the Procedure Results screen rather than being collapsed under the
 * first one's. `ProcedureService::search()` joins report to order code on
 * `procedure_order_seq`, so the sequences have to line up for the FHIR read
 * to emit an Observation per analyte.
 *
 * UUIDs are written here rather than left to
 * `ProcedureService::__construct`'s backfill. The backfill works — the
 * spike confirmed it — but it means the first reader of a row performs a
 * write, and a row this endpoint reports ids for should be complete when
 * the transaction commits.
 */
final readonly class ProcedureChainWriter implements ProcedureChain
{
    /**
     * `procedure_order.procedure_order_type`. The value OpenEMR's own
     * order entry uses for a lab test.
     */
    private const ORDER_TYPE = 'laboratory_test';

    /**
     * @param list<LabWriteBackResult> $results
     * @return list<ProcedureChainIds>
     * @throws WriteBackFailed if given no results
     */
    public function write(array $results, int $pid, int $userId, int $documentId): array
    {
        if ($results === []) {
            throw new WriteBackFailed('the procedure chain needs at least one result');
        }
        // The order spans the panel, so its dates come from the earliest
        // collection and the latest report across the rows it covers.
        $collected = min(array_map(static fn(LabWriteBackResult $r): string => $r->collectionDate->format('Y-m-d H:i:s'), $results));
        $reported = max(array_map(
            static fn(LabWriteBackResult $r): string => ($r->reportedAt ?? $r->collectionDate)->format('Y-m-d H:i:s'),
            $results
        ));

        $orderId = QueryUtils::sqlInsert(
            'INSERT INTO `procedure_order` SET `uuid` = ?, `provider_id` = ?, `patient_id` = ?,'
            . ' `encounter_id` = 0, `date_collected` = ?, `date_ordered` = ?,'
            . " `order_status` = 'completed', `activity` = 1, `procedure_order_type` = ?,"
            . ' `lab_id` = 0',
            [
                (new UuidRegistry(['table_name' => 'procedure_order']))->createUuid(),
                $userId,
                $pid,
                $collected,
                $reported,
                self::ORDER_TYPE,
            ]
        );

        $written = [];
        foreach ($results as $index => $result) {
            $written[] = $this->writeResult($result, $orderId, $index + 1, $userId, $documentId);
        }
        return $written;
    }

    private function writeResult(
        LabWriteBackResult $result,
        int $orderId,
        int $sequence,
        int $userId,
        int $documentId
    ): ProcedureChainIds {
        $collected = $result->collectionDate->format('Y-m-d H:i:s');
        $reported = ($result->reportedAt ?? $result->collectionDate)->format('Y-m-d H:i:s');

        QueryUtils::sqlStatementThrowException(
            'INSERT INTO `procedure_order_code` SET `procedure_order_id` = ?,'
            . ' `procedure_order_seq` = ?, `procedure_code` = ?, `procedure_name` = ?,'
            . " `procedure_source` = '1', `do_not_send` = 0",
            [$orderId, $sequence, $result->testCode, $result->testName]
        );

        $reportId = QueryUtils::sqlInsert(
            'INSERT INTO `procedure_report` SET `uuid` = ?, `procedure_order_id` = ?,'
            . ' `procedure_order_seq` = ?, `date_collected` = ?, `date_report` = ?,'
            . " `source` = ?, `report_status` = 'final', `review_status` = 'received'",
            [
                (new UuidRegistry(['table_name' => 'procedure_report']))->createUuid(),
                $orderId,
                $sequence,
                $collected,
                $reported,
                $userId,
            ]
        );

        $resultId = QueryUtils::sqlInsert(
            'INSERT INTO `procedure_result` SET `uuid` = ?, `procedure_report_id` = ?,'
            . ' `result_data_type` = ?, `result_code` = ?, `result_text` = ?, `date` = ?,'
            . ' `units` = ?, `result` = ?, `range` = ?, `abnormal` = ?, `comments` = ?,'
            . " `document_id` = ?, `result_status` = 'final'",
            [
                (new UuidRegistry(['table_name' => 'procedure_result']))->createUuid(),
                $reportId,
                $result->value->toOpenEmrDataType(),
                $result->testCode,
                $result->testName,
                $reported,
                $result->unit ?? '',
                $result->value->toOpenEmrResult(),
                $result->referenceRange?->toOpenEmrRange() ?? '',
                $result->abnormal->toOpenEmrCode(),
                // D1: an extraction id and nothing else. This column is
                // clinician-visible text and surfaces as
                // Observation.note[0].text; the ledger holds the document,
                // page and bbox it maps to.
                $result->extractionId,
                // Correct, and invisible over FHIR: the lab Observation
                // service never emits derivedFrom (spike §6). Populated
                // because it is the schema's own link and a direct query
                // can follow it.
                $documentId,
            ]
        );

        return new ProcedureChainIds($orderId, $reportId, $resultId);
    }
}
