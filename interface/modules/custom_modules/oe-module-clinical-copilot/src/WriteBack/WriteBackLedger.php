<?php

/**
 * The write-back ledger table: D1's idempotency record.
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
use Psr\Clock\ClockInterface;

/**
 * D1: "Idempotency is a ledger table in the fork with a UNIQUE key over
 * (document sha3-512, test code, collection date)", and "the ledger maps
 * that extraction id to document id, page and bbox".
 *
 * The UNIQUE constraint is the enforcement. The pre-insert lookup is an
 * optimisation and a source of ids for the response; it is not what makes
 * the endpoint idempotent, because two concurrent identical requests can
 * both miss it. The constraint is what they then collide on.
 *
 * Every column here is an identifier, a coordinate or a date. No test
 * value, no patient name — the same rule CLAUDE.md invariant 1 applies to
 * logs, applied to a table that exists to be read while debugging. The one
 * clinical-looking column is `test_code`, which is a LOINC code and is
 * part of the key.
 */
final readonly class WriteBackLedger
{
    public const TABLE = 'module_clinical_copilot_writeback_ledger';

    public function __construct(private ClockInterface $clock)
    {
    }

    public function findByIdempotencyKey(string $idempotencyKey): ?LedgerEntry
    {
        $row = QueryUtils::querySingleRow(
            'SELECT `idempotency_key`, `extraction_id`, `document_id`, `procedure_order_id`,'
            . ' `procedure_report_id`, `procedure_result_id`'
            . ' FROM `' . self::TABLE . '` WHERE `idempotency_key` = ?',
            [$idempotencyKey]
        );
        if (!is_array($row) || $row === []) {
            return null;
        }
        return new LedgerEntry(
            Row::string($row, 'idempotency_key'),
            Row::string($row, 'extraction_id'),
            Row::int($row, 'document_id'),
            Row::int($row, 'procedure_order_id'),
            Row::int($row, 'procedure_report_id'),
            Row::int($row, 'procedure_result_id'),
        );
    }

    /**
     * Records what this write did. Returns the ledger row id.
     *
     * A duplicate key here is a race, not a bug: another request wrote the
     * same result between our lookup and this insert. The caller rolls
     * back and re-reads, so both callers end up with one row and the same
     * ids.
     *
     * @throws \OpenEMR\Common\Database\SqlQueryException on a duplicate key
     */
    public function record(
        LabWriteBackRequest $request,
        LabWriteBackResult $result,
        int $documentId,
        ProcedureChainIds $ids,
    ): int {
        return QueryUtils::sqlInsert(
            'INSERT INTO `' . self::TABLE . '` SET'
            . ' `idempotency_key` = ?, `extraction_id` = ?, `document_sha3_512` = ?,'
            . ' `document_id` = ?, `patient_uuid` = ?, `test_code` = ?, `collection_date` = ?,'
            . ' `page` = ?, `bbox_x0` = ?, `bbox_y0` = ?, `bbox_x1` = ?, `bbox_y1` = ?,'
            . ' `procedure_order_id` = ?, `procedure_report_id` = ?, `procedure_result_id` = ?,'
            . ' `written_at` = ?, `extractor_version` = ?',
            [
                $result->idempotencyKey,
                $result->extractionId,
                $request->document->sha3512,
                $documentId,
                $request->patientUuid,
                $result->testCode,
                $result->collectionDate->format('Y-m-d'),
                $result->page,
                $result->bbox->x0,
                $result->bbox->y0,
                $result->bbox->x1,
                $result->bbox->y1,
                $ids->procedureOrderId,
                $ids->procedureReportId,
                $ids->procedureResultId,
                $this->clock->now()->format('Y-m-d H:i:s'),
                $request->extractorVersion,
            ]
        );
    }
}
