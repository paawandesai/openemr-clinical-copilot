<?php

/**
 * A DocumentStorage that stores in memory and records what it was asked to discard.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentStorage;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\StoredDocument;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\WriteOutcome;

final class RecordingDocumentStorage implements DocumentStorage
{
    public const STORED_PATH = '/var/lib/openemr/documents/1/fake-ciphertext';

    /** @var list<StoredDocument> */
    public array $discarded = [];

    public int $stored = 0;

    public function __construct(public ?int $existingIdForHash = null)
    {
    }

    public function resolveCategoryId(string $categoryPath): int
    {
        return 2;
    }

    public function findByHash(int $pid, int $categoryId, string $sha3512): ?int
    {
        return $this->existingIdForHash;
    }

    public function requireExisting(int $documentId, int $pid, string $sha3512): int
    {
        return $documentId;
    }

    public function store(
        int $pid,
        int $categoryId,
        string $filename,
        string $declaredMimeType,
        string $content,
        int $ownerId
    ): StoredDocument {
        $this->stored++;
        return new StoredDocument(2474, WriteOutcome::CREATED, self::STORED_PATH);
    }

    public function discardStoredFile(StoredDocument $document): void
    {
        $this->discarded[] = $document;
    }
}
