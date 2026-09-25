<?php

/**
 * What ChartDocuments asks of the `documents` table, apart from storing.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

interface ChartDocumentIndex
{
    /** Takes a named lock, waiting a few seconds at most; false if it could not. */
    public function lock(string $name): bool;

    public function unlock(string $name): void;

    /** The oldest undeleted document of this patient with this hash, in any category. */
    public function findForPatient(int $pid, string $sha3512): ?int;

    /**
     * Whether an undeleted document with this hash is filed, in one of these
     * categories, on any patient other than this one.
     *
     * @param list<int> $categoryIds
     */
    public function isFiledOnAnotherPatient(int $pid, string $sha3512, array $categoryIds): bool;

    /** Whether this document is filed in this category. */
    public function isInCategory(int $documentId, int $categoryId): bool;

    /**
     * Files the document in this category and no other, the way OpenEMR's
     * own "move to category" does. Never deletes the document.
     */
    public function moveToCategory(int $documentId, int $categoryId): void;

    /** Adds a note to the document, shown with it in the chart's Documents. */
    public function addNote(int $documentId, int $ownerId, string $note): void;
}
