<?php

/**
 * The document half of a write-back request.
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
 * Mirrors the agent-side `WriteBackDocument`
 * (agent/app/models/w2/writeback.py).
 *
 * Exactly one of `contentBase64` and `existingDocumentId`: a first attempt
 * sends the bytes, a retry after a partial failure names the document
 * OpenEMR already stored. `sha3512` is required either way — it is the
 * document idempotency key and the first component of every result's key,
 * so a request without it could not be made idempotent at all (CLAUDE.md
 * invariant 6).
 *
 * When the bytes are present they are checked against both `sha3512` and
 * `byteLength` before anything is stored. Trusting a caller's digest would
 * let a request file one document's bytes under another document's
 * idempotency key, which is a corrupted ledger rather than a rejected
 * request.
 */
final readonly class WriteBackDocument
{
    public const DEFAULT_CATEGORY_PATH = '/Categories/Lab_Report';

    private function __construct(
        public string $filename,
        public string $mimeType,
        public string $sha3512,
        public int $byteLength,
        public ?string $content,
        public ?int $existingDocumentId,
        public string $categoryPath,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function fromArray(array $payload, string $field = 'document'): self
    {
        RequestField::rejectUnknownKeys(
            $payload,
            [
                'filename', 'mime_type', 'sha3_512', 'byte_length',
                'content_base64', 'existing_document_id', 'category_path',
            ],
            $field
        );

        $sha3512 = IdempotencyKeys::documentKey(
            RequestField::string($payload, 'sha3_512', $field . '.sha3_512'),
            $field . '.sha3_512'
        );
        $byteLength = RequestField::positiveInt($payload, 'byte_length', $field . '.byte_length');
        $existingDocumentId = RequestField::optionalPositiveInt(
            $payload,
            'existing_document_id',
            $field . '.existing_document_id'
        );
        $encoded = RequestField::optionalString($payload, 'content_base64', $field . '.content_base64');

        if (($encoded === null) === ($existingDocumentId === null)) {
            throw new InvalidWriteBackRequest(
                $field,
                'send the bytes (content_base64) or name an already-stored document '
                . '(existing_document_id) — exactly one'
            );
        }

        $content = null;
        if ($encoded !== null) {
            $content = base64_decode($encoded, true);
            if ($content === false) {
                throw new InvalidWriteBackRequest($field . '.content_base64', 'is valid base64');
            }
            if (strlen($content) !== $byteLength) {
                throw new InvalidWriteBackRequest(
                    $field . '.byte_length',
                    'disagrees with the length of the decoded content_base64'
                );
            }
            if (!hash_equals($sha3512, hash('sha3-512', $content))) {
                throw new InvalidWriteBackRequest(
                    $field . '.sha3_512',
                    'is not the SHA3-512 of the decoded content_base64'
                );
            }
        }

        return new self(
            RequestField::string($payload, 'filename', $field . '.filename'),
            RequestField::string($payload, 'mime_type', $field . '.mime_type'),
            $sha3512,
            $byteLength,
            $content,
            $existingDocumentId,
            RequestField::optionalString($payload, 'category_path', $field . '.category_path')
                ?? self::DEFAULT_CATEGORY_PATH,
        );
    }
}
