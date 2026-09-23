<?php

/**
 * Shape checks for the agent's Week 2 responses. Each throws
 * \UnexpectedValueException naming the field, never its value: a value can
 * be chart content, and the exception reaches the log.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

final class AgentJson
{
    /** A snake_case code: safe to key a label on, cannot carry prose. */
    private const CODE = '/^[a-z][a-z0-9_]{0,63}$/';

    /**
     * @param array<mixed> $from
     */
    public static function string(array $from, string $key): string
    {
        $value = $from[$key] ?? null;
        if (!is_string($value) || $value === '') {
            throw new \UnexpectedValueException('Agent response field is not a non-empty string: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     */
    public static function nullableString(array $from, string $key): ?string
    {
        $value = $from[$key] ?? null;
        if ($value === null) {
            return null;
        }
        if (!is_string($value) || $value === '') {
            throw new \UnexpectedValueException('Agent response field is not a non-empty string or null: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     * @param list<string> $allowed
     */
    public static function oneOf(array $from, string $key, array $allowed): string
    {
        $value = self::string($from, $key);
        if (!in_array($value, $allowed, true)) {
            throw new \UnexpectedValueException('Agent response field is outside its closed set: ' . $key);
        }
        return $value;
    }

    /**
     * A code from a closed set the module does not enumerate (the agent's
     * RoutingReasonCode grows with the supervisor): snake_case only, so it
     * is safe to key a label on and cannot carry prose.
     *
     * @param array<mixed> $from
     */
    public static function code(array $from, string $key): string
    {
        $value = self::string($from, $key);
        if (preg_match(self::CODE, $value) !== 1) {
            throw new \UnexpectedValueException('Agent response field is not a code: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     */
    public static function int(array $from, string $key): int
    {
        $value = $from[$key] ?? null;
        if (!is_int($value) || $value < 0) {
            throw new \UnexpectedValueException('Agent response field is not a non-negative integer: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     * @return array<mixed>
     */
    public static function map(array $from, string $key): array
    {
        $value = $from[$key] ?? null;
        if (!is_array($value)) {
            throw new \UnexpectedValueException('Agent response field is not an object: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     * @return list<array<mixed>>
     */
    public static function rows(array $from, string $key): array
    {
        $value = $from[$key] ?? [];
        if (!is_array($value) || !array_is_list($value)) {
            throw new \UnexpectedValueException('Agent response field is not a list: ' . $key);
        }
        $rows = [];
        foreach ($value as $row) {
            if (!is_array($row)) {
                throw new \UnexpectedValueException('Agent response list holds a non-object: ' . $key);
            }
            $rows[] = $row;
        }
        return $rows;
    }

    /**
     * @param array<mixed> $from
     * @return list<string>
     */
    public static function codes(array $from, string $key): array
    {
        $value = $from[$key] ?? [];
        if (!is_array($value) || !array_is_list($value)) {
            throw new \UnexpectedValueException('Agent response field is not a list: ' . $key);
        }
        $codes = [];
        foreach ($value as $item) {
            if (!is_string($item) || preg_match(self::CODE, $item) !== 1) {
                throw new \UnexpectedValueException('Agent response list holds a non-code: ' . $key);
            }
            $codes[] = $item;
        }
        return $codes;
    }
}
