<?php

/**
 * Parsing helpers for the write-back request body.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use DateTimeImmutable;

/**
 * Turns `json_decode`'s `mixed` into typed values at the one boundary
 * where untyped data enters, so nothing downstream re-validates.
 *
 * The rules here are deliberately the same two the agent-side contracts
 * enforce (agent/app/models/w2/base.py): unknown fields are refused, and
 * nothing is coerced. Refusing coercion is the load-bearing half. A
 * `"142"` that silently became `142` would make the endpoint accept a
 * value the agent's own contract would have rejected, and the point of
 * writing both sides against one contract is that neither is more
 * permissive than the other. JSON integers are accepted where a float is
 * wanted, matching Pydantic's strict mode, because a whole number is a
 * valid float and JSON has no way to say otherwise.
 */
final class RequestField
{
    /**
     * @param array<string, mixed> $payload
     * @param list<string> $known
     * @throws InvalidWriteBackRequest
     */
    public static function rejectUnknownKeys(array $payload, array $known, string $field): void
    {
        $unknown = array_diff(array_keys($payload), $known);
        if ($unknown !== []) {
            sort($unknown);
            throw new InvalidWriteBackRequest(
                $field,
                'unknown field(s): ' . implode(', ', $unknown)
            );
        }
    }

    /**
     * @param array<string, mixed> $payload
     * @return array<string, mixed>
     * @throws InvalidWriteBackRequest
     */
    public static function object(array $payload, string $key, string $field): array
    {
        $value = $payload[$key] ?? null;
        if (!is_array($value)) {
            throw new InvalidWriteBackRequest($field, 'is required and is a JSON object');
        }
        /** @var array<string, mixed> $value */
        return $value;
    }

    /**
     * @param array<string, mixed> $payload
     * @return list<mixed>
     * @throws InvalidWriteBackRequest
     */
    public static function listOf(array $payload, string $key, string $field): array
    {
        $value = $payload[$key] ?? null;
        if (!is_array($value) || !array_is_list($value)) {
            throw new InvalidWriteBackRequest($field, 'is required and is a JSON array');
        }
        return $value;
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function string(array $payload, string $key, string $field): string
    {
        $value = $payload[$key] ?? null;
        if (!is_string($value) || trim($value) === '') {
            throw new InvalidWriteBackRequest($field, 'is required and is a non-empty string');
        }
        return $value;
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function optionalString(array $payload, string $key, string $field): ?string
    {
        $value = $payload[$key] ?? null;
        if ($value === null) {
            return null;
        }
        if (!is_string($value)) {
            throw new InvalidWriteBackRequest($field, 'is a string when present');
        }
        return $value;
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function matching(array $payload, string $key, string $pattern, string $field, string $shape): string
    {
        $value = self::string($payload, $key, $field);
        if (preg_match($pattern, $value) !== 1) {
            throw new InvalidWriteBackRequest($field, 'is ' . $shape);
        }
        return $value;
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function positiveInt(array $payload, string $key, string $field): int
    {
        $value = $payload[$key] ?? null;
        if (!is_int($value) || $value < 1) {
            throw new InvalidWriteBackRequest($field, 'is required and is an integer of at least 1');
        }
        return $value;
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function optionalPositiveInt(array $payload, string $key, string $field): ?int
    {
        if (($payload[$key] ?? null) === null) {
            return null;
        }
        return self::positiveInt($payload, $key, $field);
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function nonNegativeFloat(array $payload, string $key, string $field): float
    {
        $value = self::optionalFloat($payload, $key, $field);
        if ($value === null) {
            throw new InvalidWriteBackRequest($field, 'is required and is a number');
        }
        if ($value < 0) {
            throw new InvalidWriteBackRequest($field, 'is not negative');
        }
        return $value;
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function optionalFloat(array $payload, string $key, string $field): ?float
    {
        $value = $payload[$key] ?? null;
        if ($value === null) {
            return null;
        }
        if (is_int($value)) {
            return (float) $value;
        }
        if (!is_float($value) || !is_finite($value)) {
            throw new InvalidWriteBackRequest($field, 'is a finite number when present');
        }
        return $value;
    }

    /**
     * A calendar date, `YYYY-MM-DD`, with no time and no timezone.
     *
     * `createFromFormat` with a `!` prefix zeroes the time fields, and the
     * round-trip comparison rejects the dates PHP would otherwise roll
     * over — `2026-02-30` becomes March 2nd without it.
     *
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function date(array $payload, string $key, string $field): DateTimeImmutable
    {
        $value = self::string($payload, $key, $field);
        $date = DateTimeImmutable::createFromFormat('!Y-m-d', $value);
        if ($date === false || $date->format('Y-m-d') !== $value) {
            throw new InvalidWriteBackRequest($field, 'is a calendar date, YYYY-MM-DD');
        }
        return $date;
    }

    /**
     * An ISO 8601 instant, as `datetime.isoformat()` writes one.
     *
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function optionalDateTime(array $payload, string $key, string $field): ?DateTimeImmutable
    {
        $value = self::optionalString($payload, $key, $field);
        if ($value === null) {
            return null;
        }
        try {
            return new DateTimeImmutable($value);
        } catch (\Throwable) {
            throw new InvalidWriteBackRequest($field, 'is an ISO 8601 datetime when present');
        }
    }
}
