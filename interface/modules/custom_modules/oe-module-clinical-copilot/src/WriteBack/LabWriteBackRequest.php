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
 * A request carries one document and the results extracted from it — a
 * panel, in practice. They are written in one transaction, but idempotency
 * is per result: each carries its own key, and a request that repeats some
 * results and adds others writes only the new ones. The alternative,
 * replaying the whole request as a unit, would either duplicate the
 * repeated results or refuse the new ones.
 *
 * `extractorVersion` is required. `LabWriteBackLedgerRow` has always
 * needed it and, until the contract gained the field, the endpoint had to
 * record an empty string. It is also what makes `extractionId` checkable:
 * the run id is `document_extraction_id` of the document digest and this
 * version, so a caller that changes extractor without re-deriving the id is
 * refused rather than filing the results under the wrong run.
 */
final readonly class LabWriteBackRequest
{
    public const DOCUMENT_KIND_LAB_PDF = 'lab_pdf';

    /** See the check in fromArray() for why there is a ceiling at all. */
    public const MAX_RESULTS = 100;

    /** @param non-empty-list<LabWriteBackResult> $results */
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
                'extraction_id', 'extractor_version', 'correlation_id',
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
        if (count($rawResults) > self::MAX_RESULTS) {
            // Not a contract rule — a bound on one transaction, so a
            // malformed or hostile request cannot hold row locks across the
            // procedure tables for an unbounded time. Far above any real
            // panel: a comprehensive metabolic panel is 14 analytes.
            throw new InvalidWriteBackRequest(
                'results',
                'carries at most ' . self::MAX_RESULTS . ' results in one request'
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
        // After the loop, so the emptiness check is also what proves the
        // list is non-empty for everything downstream.
        if ($results === []) {
            throw new InvalidWriteBackRequest('results', 'carries at least one result');
        }

        $extractorVersion = RequestField::string($payload, 'extractor_version', 'extractor_version');
        $extractionId = RequestField::matching(
            $payload,
            'extraction_id',
            IdempotencyKeys::EXTRACTION_ID_PATTERN,
            'extraction_id',
            'an extraction id, w2x- followed by 32 hex characters'
        );
        $expectedRun = IdempotencyKeys::documentExtractionId($document->sha3512, $extractorVersion);
        if (!hash_equals($expectedRun, $extractionId)) {
            throw new InvalidWriteBackRequest(
                'extraction_id',
                'is not this document extracted by this extractor_version; '
                . 'see document_extraction_id'
            );
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
            $extractionId,
            RequestField::matching(
                $payload,
                'correlation_id',
                IdempotencyKeys::CORRELATION_ID_PATTERN,
                'correlation_id',
                'a uuid'
            ),
            $extractorVersion,
        );
    }

    /** The document-level dedup key for this request. */
    public function documentKey(): string
    {
        return $this->document->sha3512;
    }
}
