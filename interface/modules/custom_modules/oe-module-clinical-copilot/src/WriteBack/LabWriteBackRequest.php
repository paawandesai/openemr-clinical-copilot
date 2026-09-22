<?php

/**
 * A parsed lab write-back request: one document plus its lab results.
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
 * Mirrors the agent-side `LabWriteBackRequest`
 * (agent/app/models/w2/writeback.py).
 *
 * `documentKind` is validated to be `lab_pdf` and nothing else. D2 forbids
 * intake medications and allergies reaching the chart — they are
 * document-derived facts, surfaced as discrepancies for a human to resolve
 * — and a type check at the boundary is the cheapest place to keep that
 * true. A future reader who does not know why the other kind is missing
 * gets told here rather than adding it.
 *
 * This first cut accepts exactly one result. Batching is deliberately out
 * of scope until the single-result path is proven end to end, and a
 * request carrying several is refused rather than silently truncated.
 *
 * `extractorVersion` is an addition to the agent-side contract, not a
 * field it sends today: `LabWriteBackLedgerRow` requires the extractor
 * version and `LabWriteBackRequest` has nowhere to carry it. The endpoint
 * accepts it when present and records an empty string otherwise, so the
 * ledger is complete the moment the contract catches up. See the session
 * report's Conflicts section.
 */
final readonly class LabWriteBackRequest
{
    public const DOCUMENT_KIND_LAB_PDF = 'lab_pdf';

    /** @param list<LabWriteBackResult> $results */
    private function __construct(
        public string $patientUuid,
        public WriteBackDocument $document,
        public array $results,
        public string $extractionId,
        public string $correlationId,
        public string $extractorVersion,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function fromArray(array $payload): self
    {
        RequestField::rejectUnknownKeys(
            $payload,
            [
                'patient_uuid', 'document_kind', 'document', 'results',
                'extraction_id', 'correlation_id', 'extractor_version',
            ],
            'request'
        );

        $documentKind = RequestField::optionalString($payload, 'document_kind', 'document_kind')
            ?? self::DOCUMENT_KIND_LAB_PDF;
        if ($documentKind !== self::DOCUMENT_KIND_LAB_PDF) {
            throw new InvalidWriteBackRequest(
                'document_kind',
                'only lab values are written back. D2: intake medications and allergies '
                . 'are document-derived facts, surfaced as discrepancies against the '
                . 'chart, never merged into it'
            );
        }

        $document = WriteBackDocument::fromArray(
            RequestField::object($payload, 'document', 'document')
        );

        $rawResults = RequestField::listOf($payload, 'results', 'results');
        if (count($rawResults) !== 1) {
            throw new InvalidWriteBackRequest(
                'results',
                'carries exactly one result: batching several into one request is not '
                . 'implemented yet, and a partial write is worse than a refused one'
            );
        }
        $results = [];
        $seen = [];
        foreach ($rawResults as $index => $rawResult) {
            $field = 'results[' . $index . ']';
            if (!is_array($rawResult)) {
                throw new InvalidWriteBackRequest($field, 'is a JSON object');
            }
            /** @var array<string, mixed> $rawResult */
            $result = LabWriteBackResult::fromArray($rawResult, $document->sha3512, $field);
            if (isset($seen[$result->idempotencyKey])) {
                throw new InvalidWriteBackRequest(
                    $field,
                    'shares an idempotency key with an earlier result in this request'
                );
            }
            $seen[$result->idempotencyKey] = true;
            $results[] = $result;
        }

        return new self(
            RequestField::matching(
                $payload,
                'patient_uuid',
                IdempotencyKeys::PATIENT_UUID_PATTERN,
                'patient_uuid',
                'a lowercase FHIR Patient uuid'
            ),
            $document,
            $results,
            RequestField::matching(
                $payload,
                'extraction_id',
                IdempotencyKeys::EXTRACTION_ID_PATTERN,
                'extraction_id',
                'an extraction id, w2x- followed by 32 hex characters'
            ),
            RequestField::matching(
                $payload,
                'correlation_id',
                IdempotencyKeys::CORRELATION_ID_PATTERN,
                'correlation_id',
                'a uuid'
            ),
            RequestField::optionalString($payload, 'extractor_version', 'extractor_version') ?? '',
        );
    }

    /** The one result this first cut writes. */
    public function onlyResult(): LabWriteBackResult
    {
        return $this->results[0];
    }

    /** The document-level dedup key for this request. */
    public function documentKey(): string
    {
        return $this->document->sha3512;
    }
}
