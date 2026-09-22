<?php

/**
 * Narrows values read out of a database row.
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
 * `QueryUtils` hands back `array<mixed>`, because the driver returns every
 * column as a string and nothing above it knows the schema. Casting at
 * each call site would satisfy the type checker and hide the one case that
 * matters — a column that is NULL, or absent because the SELECT changed —
 * behind a silent `0`.
 *
 * So these narrow rather than cast, and a value that is not what the
 * schema says it is fails loudly. Every column read through here is
 * `NOT NULL` in its table, which is what makes "unusable" mean "the query
 * and the schema disagree" rather than "this row is unremarkable".
 */
final class Row
{
    /**
     * @param array<mixed> $row
     * @throws WriteBackFailed
     */
    public static function int(array $row, string $column): int
    {
        return self::toInt($row[$column] ?? null, 'column "' . $column . '"');
    }

    /**
     * The same narrowing for a single untyped value, such as a legacy
     * accessor's return.
     *
     * @throws WriteBackFailed
     */
    public static function toInt(mixed $value, string $label): int
    {
        if (is_int($value)) {
            return $value;
        }
        if (is_string($value) && preg_match('/^-?\d+$/', $value) === 1) {
            return (int) $value;
        }
        throw new WriteBackFailed($label . ' did not come back as an integer');
    }

    /**
     * @param array<mixed> $row
     * @throws WriteBackFailed
     */
    public static function string(array $row, string $column): string
    {
        $value = $row[$column] ?? null;
        if (is_string($value)) {
            return $value;
        }
        if (is_int($value)) {
            return (string) $value;
        }
        throw new WriteBackFailed('column "' . $column . '" did not come back as a string');
    }
}
