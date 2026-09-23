<?php

/**
 * ChartDocumentIndex over OpenEMR's own database layer.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\Row;

final class QueryUtilsChartDocumentIndex implements ChartDocumentIndex
{
    /** Long enough for a concurrent upload's store to finish, short of PHP's own limit. */
    private const LOCK_SECONDS = 10;

    public function lock(string $name): bool
    {
        $row = QueryUtils::querySingleRow('SELECT GET_LOCK(?, ?) AS `acquired`', [$name, self::LOCK_SECONDS]);
        // GET_LOCK answers 1 (acquired), 0 (timed out) or NULL (error); the
        // driver may hand the 1 back as an int or a string.
        $acquired = is_array($row) ? ($row['acquired'] ?? null) : null;
        return $acquired === 1 || $acquired === '1';
    }

    public function unlock(string $name): void
    {
        QueryUtils::querySingleRow('SELECT RELEASE_LOCK(?) AS `released`', [$name]);
    }

    public function findForPatient(int $pid, string $sha3512): ?int
    {
        $row = QueryUtils::querySingleRow(
            'SELECT `id` FROM `documents` WHERE `foreign_id` = ? AND `hash` = ? AND `deleted` = 0'
            . ' ORDER BY `id` ASC LIMIT 1',
            [$pid, $sha3512]
        );
        return (is_array($row) && isset($row['id'])) ? Row::int($row, 'id') : null;
    }

    public function isFiledOnAnotherPatient(int $pid, string $sha3512, array $categoryIds): bool
    {
        if ($categoryIds === []) {
            return false;
        }
        $placeholders = implode(', ', array_fill(0, count($categoryIds), '?'));
        $row = QueryUtils::querySingleRow(
            'SELECT d.`id` FROM `documents` d'
            . ' JOIN `categories_to_documents` ctd ON ctd.`document_id` = d.`id`'
            . ' WHERE d.`hash` = ? AND d.`foreign_id` <> ? AND d.`deleted` = 0'
            . ' AND ctd.`category_id` IN (' . $placeholders . ') LIMIT 1',
            array_merge([$sha3512, $pid], $categoryIds)
        );
        return is_array($row) && isset($row['id']);
    }
}
