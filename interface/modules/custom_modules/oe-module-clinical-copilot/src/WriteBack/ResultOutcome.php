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
 * create some rows, recognise others as already written under this file,
 * find others already in the chart under another, and decline to overwrite
 * the rest.
 *
 * `ids` is the row this call made (`created`), the row an earlier call for
 * the same file made (`duplicate_ignored`), or the chart row this result
 * matched (`already_in_chart`). A `conflict` wrote nothing and has none;
 * it names the chart row and value it disagrees with instead.
 */
final readonly class ResultOutcome
{
    public function __construct(
        public string $testCode,
        public string $idempotencyKey,
        public string $extractionId,
        public WriteOutcome $outcome,
        public ?ProcedureChainIds $ids,
        public ?int $matchedProcedureResultId = null,
        public ?string $chartValue = null,
        public ?string $chartUnit = null,
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

    /** The chart already holds this value: the ids are the existing row's. */
    public static function alreadyInChart(LabWriteBackResult $result, ChartResult $existing): self
    {
        return new self(
            $result->testCode,
            $result->idempotencyKey,
            $result->extractionId,
            WriteOutcome::ALREADY_IN_CHART,
            $existing->ids(),
            $existing->procedureResultId,
        );
    }

    /** The chart holds a different value: nothing written, both sides reported. */
    public static function conflict(LabWriteBackResult $result, ChartResult $existing): self
    {
        return new self(
            $result->testCode,
            $result->idempotencyKey,
            $result->extractionId,
            WriteOutcome::CONFLICT,
            null,
            $existing->procedureResultId,
            $existing->result,
            $existing->units,
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
            'procedure_report_id' => $this->ids?->procedureReportId,
            'procedure_result_id' => $this->ids?->procedureResultId,
            'matched_procedure_result_id' => $this->matchedProcedureResultId,
            'chart_value' => $this->chartValue,
            'chart_unit' => $this->chartUnit,
        ];
    }
}
