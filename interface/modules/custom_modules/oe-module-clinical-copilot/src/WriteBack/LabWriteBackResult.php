<?php

/**
 * One lab value to write back.
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

/**
 * Mirrors the agent-side `LabWriteBackResult`
 * (agent/app/models/w2/writeback.py): everything the four-table insert
 * needs, and everything the ledger needs to trace the value back to a page.
 *
 * `testCode` and `testName` are both required because OpenEMR's FHIR read
 * drops the code and substitutes a null flavor unless
 * `procedure_result.result_code` and `result_text` are both populated
 * (docs/w2/spikes/writeback.md §9).
 */
final readonly class LabWriteBackResult
{
    private function __construct(
        public string $testCode,
        public string $testName,
        public LabValue $value,
        public ?string $unit,
        public ?ReferenceRange $referenceRange,
        public DateTimeImmutable $collectionDate,
        /** The collection time the report printed, on `collectionDate`; null when it printed none. */
        public ?DateTimeImmutable $collectedAt,
        public ?DateTimeImmutable $reportedAt,
        public AbnormalFlag $abnormal,
        public int $page,
        public BBox $bbox,
        public string $idempotencyKey,
        public string $extractionId,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function fromArray(array $payload, string $documentSha3512, string $field): self
    {
        RequestField::rejectUnknownKeys(
            $payload,
            [
                'test_code', 'test_name', 'value', 'unit', 'reference_range',
                'collection_date', 'collected_at', 'reported_at', 'abnormal', 'page', 'bbox',
                'idempotency_key', 'extraction_id',
            ],
            $field
        );

        $testCode = RequestField::string($payload, 'test_code', $field . '.test_code');
        $collectionDate = RequestField::date($payload, 'collection_date', $field . '.collection_date');
        // A time on the collection date, or nothing: the chart match uses
        // it, and the ledger key (date only, D1) does not.
        $collectedAt = RequestField::optionalDateTime($payload, 'collected_at', $field . '.collected_at');
        if ($collectedAt !== null && $collectedAt->format('Y-m-d') !== $collectionDate->format('Y-m-d')) {
            throw new InvalidWriteBackRequest($field . '.collected_at', 'is a time on collection_date');
        }
        $abnormal = AbnormalFlag::tryFrom(
            RequestField::string($payload, 'abnormal', $field . '.abnormal')
        );
        if ($abnormal === null) {
            throw new InvalidWriteBackRequest(
                $field . '.abnormal',
                'is one of: normal, high, low, abnormal, indeterminate'
            );
        }
        // Raises for a flag procedure_result.abnormal cannot hold, here at
        // the boundary rather than part-way through the insert.
        $abnormal->toOpenEmrCode();

        $page = RequestField::positiveInt($payload, 'page', $field . '.page');
        $bbox = BBox::fromArray(
            RequestField::object($payload, 'bbox', $field . '.bbox'),
            $field . '.bbox'
        );
        if ($bbox->page !== $page) {
            throw new InvalidWriteBackRequest($field . '.bbox.page', 'disagrees with page');
        }

        $referenceRangePayload = $payload['reference_range'] ?? null;
        $referenceRange = $referenceRangePayload === null
            ? null
            : ReferenceRange::fromArray(
                RequestField::object($payload, 'reference_range', $field . '.reference_range'),
                $field . '.reference_range'
            );

        $expectedKey = IdempotencyKeys::labResultKey($documentSha3512, $testCode, $collectionDate);
        $key = RequestField::string($payload, 'idempotency_key', $field . '.idempotency_key');
        if (!hash_equals($expectedKey, $key)) {
            throw new InvalidWriteBackRequest(
                $field . '.idempotency_key',
                'is not the composition of (document sha3-512, test code, collection date)'
            );
        }
        $expectedExtractionId = IdempotencyKeys::labResultExtractionId(
            $documentSha3512,
            $testCode,
            $collectionDate
        );
        $extractionId = RequestField::matching(
            $payload,
            'extraction_id',
            IdempotencyKeys::EXTRACTION_ID_PATTERN,
            $field . '.extraction_id',
            'an extraction id, w2x- followed by 32 hex characters'
        );
        if (!hash_equals($expectedExtractionId, $extractionId)) {
            throw new InvalidWriteBackRequest(
                $field . '.extraction_id',
                'is not derived from this result\'s idempotency key'
            );
        }

        return new self(
            $testCode,
            RequestField::string($payload, 'test_name', $field . '.test_name'),
            LabValue::fromArray(
                RequestField::object($payload, 'value', $field . '.value'),
                $field . '.value'
            ),
            RequestField::optionalString($payload, 'unit', $field . '.unit'),
            $referenceRange,
            $collectionDate,
            $collectedAt,
            RequestField::optionalDateTime($payload, 'reported_at', $field . '.reported_at'),
            $abnormal,
            $page,
            $bbox,
            $key,
            $extractionId,
        );
    }
}
