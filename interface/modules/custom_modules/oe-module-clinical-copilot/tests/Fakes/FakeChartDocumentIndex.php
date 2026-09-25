<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Common\Database\SqlQueryException;
use OpenEMR\Modules\ClinicalCopilot\W2\ChartDocumentIndex;

/**
 * The index, answering from constructor arguments and recording the lock,
 * every category move and every note.
 */
final class FakeChartDocumentIndex implements ChartDocumentIndex
{
    /** @var list<string> */
    public array $lockEvents = [];

    /** @var list<array{int, int}> documentId, categoryId */
    public array $moves = [];

    /** @var list<array{int, int, string}> documentId, ownerId, note */
    public array $notes = [];

    /** @var list<array{int, list<int>}> pid, categoryIds */
    public array $elsewhereQueries = [];

    /**
     * @param list<int> $categoriesOfExisting Categories the existing document is filed in.
     */
    public function __construct(
        private ?int $existing = null,
        private bool $elsewhere = false,
        private bool $lockable = true,
        private array $categoriesOfExisting = [2],
        private bool $moveFails = false,
    ) {
    }

    public function lock(string $name): bool
    {
        if ($this->lockable) {
            $this->lockEvents[] = 'lock';
        }
        return $this->lockable;
    }

    public function unlock(string $name): void
    {
        $this->lockEvents[] = 'unlock';
    }

    public function findForPatient(int $pid, string $sha3512): ?int
    {
        return $this->existing;
    }

    public function isFiledOnAnotherPatient(int $pid, string $sha3512, array $categoryIds): bool
    {
        $this->elsewhereQueries[] = [$pid, $categoryIds];
        return $this->elsewhere;
    }

    public function isInCategory(int $documentId, int $categoryId): bool
    {
        return $documentId === $this->existing && in_array($categoryId, $this->categoriesOfExisting, true);
    }

    public function moveToCategory(int $documentId, int $categoryId): void
    {
        if ($this->moveFails) {
            throw new SqlQueryException('REPLACE INTO categories_to_documents', 'database unavailable');
        }
        $this->moves[] = [$documentId, $categoryId];
        $this->categoriesOfExisting = [$categoryId];
    }

    public function addNote(int $documentId, int $ownerId, string $note): void
    {
        $this->notes[] = [$documentId, $ownerId, $note];
    }
}
