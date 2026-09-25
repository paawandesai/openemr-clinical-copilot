<?php

/**
 * Where a document's extraction has got to, as the agent reports it from
 * `POST /documents` (UploadAccepted) or `GET /documents/{id}/extraction`
 * (ExtractionState) — agent/app/api/documents.py.
 *
 * `status` IS RENAMED. The agent's responses have no envelope and call the
 * extraction state `status`; every module response is wrapped in the
 * `{"status": "ok"|"error"}` envelope, and the browser keeps polling on the
 * extraction state. Both on one field would let an envelope value stop, or
 * fail to stop, the poll (docs/w2/ui-integration.md §2.0). So the card sees
 * `extraction_status`, and never the agent's `status`.
 *
 * `identity_check` (D60) is the agent's closed-set verdict on whether the
 * document is this chart's patient's, set only once complete. `mismatch` is
 * what the upload and status routes quarantine on; the card then shows the
 * `quarantine` outcome they add, and does not offer the document for
 * questions.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

final readonly class ExtractionState
{
    /** `ExtractionStatus` (agent/app/models/w2/documents.py). */
    public const STATUSES = ['pending', 'in_progress', 'complete', 'failed'];

    /** `ExtractionFailureReason` (agent/app/models/w2/workers.py). */
    public const FAILURE_REASONS = [
        'unreadable_file',
        'unsupported_mime_type',
        'no_text_layer_and_ocr_failed',
        'model_output_invalid',
        'page_limit_exceeded',
        'timed_out',
        'vision_call_failed',
        'no_fields_located',
        'internal_error',
        'interrupted',
    ];

    /** `IdentityOutcome` (agent/app/api/documents.py). */
    public const IDENTITY_CHECKS = ['mismatch', 'confirmed', 'not_confirmed'];

    private function __construct(
        public AgentDocumentId $documentId,
        public DocumentKind $kind,
        public string $extractionStatus,
        public ?string $extractionId,
        public ?string $failureReason,
        public ?int $byteLength,
        public string $correlationId,
        public ?string $identityCheck = null,
    ) {
    }

    /** The agent found another patient's name or date of birth on it: quarantine it (D60). */
    public function isIdentityMismatch(): bool
    {
        return $this->extractionStatus === 'complete' && $this->identityCheck === 'mismatch';
    }

    /**
     * `POST /documents`'s 200. The id must be the digest of the bytes the
     * module sent: an agent that answered for other bytes answered for
     * another document.
     *
     * @param array<mixed> $body
     * @throws \UnexpectedValueException
     */
    public static function fromUpload(array $body, AgentDocumentId $sent): self
    {
        $state = self::parse($body, $sent);
        return new self(
            $state->documentId,
            $state->kind,
            $state->extractionStatus,
            null,
            null,
            AgentJson::int($body, 'byte_length'),
            $state->correlationId,
            $state->identityCheck,
        );
    }

    /**
     * `GET /documents/{id}/extraction`'s 200.
     *
     * @param array<mixed> $body
     * @throws \UnexpectedValueException
     */
    public static function fromStatus(array $body, AgentDocumentId $asked): self
    {
        return self::parse($body, $asked);
    }

    /**
     * @param ?QuarantineOutcome $quarantine What the route did about a mismatch; null when there was none.
     * @return array{status: 'ok', document_id: string, kind: string, extraction_status: string, byte_length: int, correlation_id: string, identity_check: ?string, quarantine: ?string}
     */
    public function toUploadPayload(?QuarantineOutcome $quarantine = null): array
    {
        return [
            'status' => 'ok',
            'document_id' => $this->documentId->value,
            'kind' => $this->kind->value,
            'extraction_status' => $this->extractionStatus,
            'byte_length' => $this->byteLength ?? 0,
            'correlation_id' => $this->correlationId,
            'identity_check' => $this->identityCheck,
            'quarantine' => $quarantine?->value,
        ];
    }

    /**
     * @param ?QuarantineOutcome $quarantine What the route did about a mismatch; null when there was none.
     * @return array{status: 'ok', document_id: string, kind: string, extraction_status: string, extraction_id: ?string, failure_reason: ?string, correlation_id: string, identity_check: ?string, quarantine: ?string}
     */
    public function toStatusPayload(?QuarantineOutcome $quarantine = null): array
    {
        return [
            'status' => 'ok',
            'document_id' => $this->documentId->value,
            'kind' => $this->kind->value,
            'extraction_status' => $this->extractionStatus,
            'extraction_id' => $this->extractionId,
            'failure_reason' => $this->failureReason,
            'correlation_id' => $this->correlationId,
            'identity_check' => $this->identityCheck,
            'quarantine' => $quarantine?->value,
        ];
    }

    /**
     * @param array<mixed> $body
     * @throws \UnexpectedValueException
     */
    private static function parse(array $body, AgentDocumentId $expected): self
    {
        if (AgentJson::string($body, 'document_id') !== $expected->value) {
            throw new \UnexpectedValueException('Agent answered for a different document');
        }
        $kind = DocumentKind::tryFrom(AgentJson::string($body, 'kind'));
        if ($kind === null) {
            throw new \UnexpectedValueException('Agent response field is outside its closed set: kind');
        }
        $status = AgentJson::oneOf($body, 'status', self::STATUSES);
        $extractionId = AgentJson::nullableString($body, 'extraction_id');
        $failureReason = AgentJson::nullableString($body, 'failure_reason');
        // The two invariants the browser relies on: an extraction id only
        // once complete, a failure reason only once failed.
        if ($extractionId !== null && $status !== 'complete') {
            throw new \UnexpectedValueException('Agent reported an extraction id for an unfinished extraction');
        }
        if ($failureReason !== null && ($status !== 'failed' || !in_array($failureReason, self::FAILURE_REASONS, true))) {
            throw new \UnexpectedValueException('Agent reported a failure reason it may not');
        }
        // Absent from an agent older than D60: the same as null.
        $identityCheck = AgentJson::nullableString($body, 'identity_check');
        if ($identityCheck !== null && ($status !== 'complete' || !in_array($identityCheck, self::IDENTITY_CHECKS, true))) {
            throw new \UnexpectedValueException('Agent reported an identity check it may not');
        }
        return new self(
            $expected,
            $kind,
            $status,
            $extractionId,
            $failureReason,
            null,
            AgentJson::string($body, 'correlation_id'),
            $identityCheck,
        );
    }
}
