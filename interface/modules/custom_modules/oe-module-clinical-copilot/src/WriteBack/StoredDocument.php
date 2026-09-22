<?php

/**
 * A document this call stored, and where its bytes landed.
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
 * The path is here for one reason: a filesystem write is not part of a
 * database transaction. `\Document::createDocument()` encrypts the bytes and
 * puts them on disk before the `documents` row is committed, so a rollback
 * after that point removes the row and leaves the ciphertext behind,
 * referenced by nothing. Knowing where the file went is what lets the
 * service clean it up.
 *
 * `path` is null when the document was already stored — there is nothing
 * this call may delete.
 */
final readonly class StoredDocument
{
    public function __construct(
        public int $documentId,
        public WriteOutcome $outcome,
        public ?string $path = null,
    ) {
    }

    public static function reused(int $documentId): self
    {
        return new self($documentId, WriteOutcome::DUPLICATE_IGNORED, null);
    }
}
