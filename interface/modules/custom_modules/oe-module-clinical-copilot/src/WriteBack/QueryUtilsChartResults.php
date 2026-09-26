<?php

/**
 * ChartResults over OpenEMR's procedure tables.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use DateTimeImmutable;
use OpenEMR\Common\Database\QueryUtils;

/**
 * `procedure_result` -> `procedure_report` -> `procedure_order`, the join
 * `ProcedureService::search()` makes for the FHIR read, filtered to one
 * patient, one code and one collection date. The code is compared the way
 * the ledger key canonicalises it (upper-cased, trimmed), so `2345-7` and
 * ` 2345-7 ` are one test. The collection date is the report's, falling
 * back to the order's, which is where a result from a lab interface or a
 * hand entry carries it. Deleted orders (`activity = 0`) are not results.
 */
final readonly class QueryUtilsChartResults implements ChartResults
{
    /** @return list<ChartResult> */
    public function findCollected(
        int $pid,
        string $testCode,
        DateTimeImmutable $collectionDate,
        ?DateTimeImmutable $collectedAt
    ): array {
        $sql = 'SELECT po.`procedure_order_id`, prp.`procedure_report_id`, pr.`procedure_result_id`,'
            . ' pr.`result`, pr.`units`,'
            . ' COALESCE(prp.`date_collected`, po.`date_collected`) AS `collected`'
            . ' FROM `procedure_result` pr'
            . ' JOIN `procedure_report` prp ON prp.`procedure_report_id` = pr.`procedure_report_id`'
            . ' JOIN `procedure_order` po ON po.`procedure_order_id` = prp.`procedure_order_id`'
            . ' WHERE po.`patient_id` = ? AND po.`activity` = 1'
            . ' AND UPPER(TRIM(pr.`result_code`)) = ?'
            . ' AND DATE(COALESCE(prp.`date_collected`, po.`date_collected`)) = ?';
        $binds = [$pid, strtoupper(trim($testCode)), $collectionDate->format('Y-m-d')];
        if ($collectedAt !== null) {
            $sql .= ' AND (COALESCE(prp.`date_collected`, po.`date_collected`) = ?'
                . " OR TIME(COALESCE(prp.`date_collected`, po.`date_collected`)) = '00:00:00')";
            $binds[] = $collectedAt->format('Y-m-d H:i:s');
        }
        $sql .= ' ORDER BY pr.`procedure_result_id`';

        $results = [];
        foreach (QueryUtils::fetchRecords($sql, $binds) as $row) {
            $collected = $row['collected'] ?? null;
            $results[] = new ChartResult(
                Row::int($row, 'procedure_order_id'),
                Row::int($row, 'procedure_report_id'),
                Row::int($row, 'procedure_result_id'),
                Row::string($row, 'result'),
                Row::string($row, 'units'),
                is_string($collected) && $collected !== '' ? new DateTimeImmutable($collected) : null,
            );
        }
        return $results;
    }
}
