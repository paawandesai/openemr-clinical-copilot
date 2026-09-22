<?php

/**
 * Stores the source document through OpenEMR's own document code path.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use Document;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Services\DocumentService;
use Psr\Log\LoggerInterface;

/**
 * Everything here goes through `\Document::createDocument()` — the same
 * call `DocumentService::insertAtPath()` makes behind
 * `POST /api/patient/:pid/document`. That matters for one reason above
 * all: `createDocument()` is where `drive_encryption` is honoured, so the
 * bytes land on disk as ciphertext. Writing the file directly, or
 * inserting a `documents` row by hand, would put a lab report on the disk
 * in the clear.
 *
 * Two behaviours of the core path are deliberately not reproduced:
 *
 * - It returns `true` rather than an id. Here `Document::get_id()` is read
 *   straight after the insert, which is what lets the endpoint answer with
 *   a real `documents.id` (D1).
 * - A rejected upload and an empty result are the same empty 404 from
 *   outside (spike §10.6). Here each refusal is its own message naming its
 *   own cause.
 */
final readonly class DocumentStore implements DocumentStorage
{
    public function __construct(
        private DocumentService $documents,
        private OEGlobalsBag $globals,
        private LoggerInterface $logger,
    ) {
    }

    /**
     * The `categories.id` a category path names, e.g.
     * `/Categories/Lab_Report`.
     *
     * @throws InvalidWriteBackRequest
     */
    public function resolveCategoryId(string $categoryPath): int
    {
        if (!$this->documents->isValidPath($categoryPath)) {
            throw new InvalidWriteBackRequest(
                'document.category_path',
                'names a document category that exists on this instance'
            );
        }
        $categoryId = $this->documents->getLastIdOfPath($categoryPath);
        if (!is_numeric($categoryId)) {
            throw new InvalidWriteBackRequest(
                'document.category_path',
                'names a document category that exists on this instance'
            );
        }
        return (int) $categoryId;
    }

    /**
     * An existing, undeleted document for this patient and category whose
     * stored hash matches.
     *
     * D1: "Document-level dedup uses OpenEMR's own stored hash, checked
     * before insert." OpenEMR computes that hash with SHA3-512 but does
     * not deduplicate on it and puts no unique constraint on the column —
     * the spike uploaded one file twice and got two rows (§7) — so the
     * check is ours to make.
     */
    public function findByHash(int $pid, int $categoryId, string $sha3512): ?int
    {
        $row = QueryUtils::querySingleRow(
            'SELECT d.`id` FROM `documents` d'
            . ' JOIN `categories_to_documents` ctd ON ctd.`document_id` = d.`id`'
            . ' WHERE ctd.`category_id` = ? AND d.`foreign_id` = ? AND d.`deleted` = 0'
            . ' AND d.`hash` = ? ORDER BY d.`id` ASC LIMIT 1',
            [$categoryId, $pid, $sha3512]
        );
        return (is_array($row) && isset($row['id'])) ? Row::int($row, 'id') : null;
    }

    /**
     * A document the caller named by id, checked to belong to this patient
     * and to be the file its digest claims.
     *
     * @throws InvalidWriteBackRequest
     */
    public function requireExisting(int $documentId, int $pid, string $sha3512): int
    {
        $row = QueryUtils::querySingleRow(
            'SELECT `id`, `hash` FROM `documents` WHERE `id` = ? AND `foreign_id` = ? AND `deleted` = 0',
            [$documentId, $pid]
        );
        if (!is_array($row) || !isset($row['id'])) {
            throw new InvalidWriteBackRequest(
                'document.existing_document_id',
                'names a stored, undeleted document belonging to this patient'
            );
        }
        if (!hash_equals($sha3512, strtolower(Row::string($row, 'hash')))) {
            throw new InvalidWriteBackRequest(
                'document.sha3_512',
                'does not match the stored hash of the document named by existing_document_id'
            );
        }
        return Row::int($row, 'id');
    }

    /**
     * Stores the bytes and returns the new `documents.id`.
     *
     * The content sniff repeats what `DocumentService::insertAtPath()`
     * does when the `secure_upload` global is on, because this path does
     * not go through that method and the check is a security control
     * rather than a convenience: `isWhiteFile()` reads the file's actual
     * magic bytes, so a PDF-named file full of something else is refused
     * here rather than stored.
     *
     * @throws InvalidWriteBackRequest|WriteBackFailed
     */
    public function store(
        int $pid,
        int $categoryId,
        string $filename,
        string $declaredMimeType,
        string $content,
        int $ownerId
    ): StoredDocument {
        $sniffed = $this->sniffMimeType($content);
        if ($sniffed !== $declaredMimeType) {
            throw new InvalidWriteBackRequest(
                'document.mime_type',
                'disagrees with the content: the bytes sniff as "' . $sniffed . '"'
            );
        }

        $document = new Document();
        // createDocument() types $patient_id as string in its docblock and
        // passes it straight into a path calculation and a bound parameter.
        $error = $document->createDocument(
            (string) $pid,
            $categoryId,
            $filename,
            $declaredMimeType,
            $content,
            owner: $ownerId
        );
        if ($error !== '') {
            // Document::createDocument() answers with an xl()'d reason
            // string. It is a storage failure, not caller input, so it is
            // logged by the caller and not echoed back.
            throw new WriteBackFailed('the document could not be stored: ' . $error);
        }

        $documentId = Row::toInt($document->get_id(), 'the stored document id');
        if ($documentId < 1) {
            throw new WriteBackFailed('the document was stored but no id came back');
        }
        return new StoredDocument($documentId, WriteOutcome::CREATED, self::pathOf($document->get_url()));
    }

    /**
     * Deletes a file `store()` wrote, after the transaction that would have
     * referenced it rolled back.
     *
     * Best effort by design. The `documents` row is gone, so nothing points
     * at these bytes and failing to remove them is a disk-space problem, not
     * a correctness one — whereas throwing here would replace the real error
     * the caller is already handling with a less useful one. The failure is
     * logged with the path and no patient identifier.
     */
    public function discardStoredFile(StoredDocument $document): void
    {
        if ($document->path === null) {
            return;
        }
        if (!is_file($document->path)) {
            return;
        }
        if (!@unlink($document->path)) {
            $this->logger->error('Clinical Co-Pilot could not remove an orphaned document file', [
                'path' => $document->path,
                'document_id' => $document->documentId,
            ]);
        }
    }

    /**
     * `documents.url` is a `file://` URL built by string concatenation, not
     * by a URL builder, so it is unwrapped the same way rather than parsed.
     */
    private static function pathOf(mixed $url): ?string
    {
        if (!is_string($url) || !str_starts_with($url, 'file://')) {
            return null;
        }
        return substr($url, strlen('file://'));
    }

    /**
     * The file's type as OpenEMR's own upload gate would see it, refused
     * if it is not on the `files_white_list`.
     *
     * @throws InvalidWriteBackRequest|WriteBackFailed
     */
    private function sniffMimeType(string $content): string
    {
        $temporaryPath = tempnam(sys_get_temp_dir(), 'oe-copilot-writeback-');
        if ($temporaryPath === false) {
            throw new WriteBackFailed('no temporary file could be created to check the upload');
        }
        try {
            if (file_put_contents($temporaryPath, $content) === false) {
                throw new WriteBackFailed('the upload could not be written for checking');
            }
            if ($this->globals->getBoolean('secure_upload') && !isWhiteFile($temporaryPath)) {
                throw new InvalidWriteBackRequest(
                    'document.mime_type',
                    'is not an accepted upload type on this instance'
                );
            }
            $sniffed = mime_content_type($temporaryPath);
            if ($sniffed === false) {
                throw new InvalidWriteBackRequest('document.mime_type', 'could not be determined from the content');
            }
            return $sniffed;
        } finally {
            unlink($temporaryPath);
        }
    }
}
