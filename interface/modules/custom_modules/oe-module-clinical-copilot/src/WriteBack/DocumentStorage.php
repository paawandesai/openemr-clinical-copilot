<?php

/**
 * Where the source document is stored and found again.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

interface DocumentStorage
{
    /** @throws InvalidWriteBackRequest */
    public function resolveCategoryId(string $categoryPath): int;

    /** An existing, undeleted document for this patient and category whose stored hash matches. */
    public function findByHash(int $pid, int $categoryId, string $sha3512): ?int;

    /** @throws InvalidWriteBackRequest */
    public function requireExisting(int $documentId, int $pid, string $sha3512): int;

    /** @throws InvalidWriteBackRequest|WriteBackFailed */
    public function store(
        int $pid,
        int $categoryId,
        string $filename,
        string $declaredMimeType,
        string $content,
        int $ownerId
    ): StoredDocument;

    /**
     * Removes a file this call wrote, after the transaction that would have
     * referenced it rolled back. See LabWriteBackService on why this exists.
     */
    public function discardStoredFile(StoredDocument $document): void;
}
