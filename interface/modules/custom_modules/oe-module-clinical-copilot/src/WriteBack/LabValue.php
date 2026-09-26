<?php

/**
 * The value of a lab result: a number, a censored number, or a term.
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
 * The PHP side of the agent's discriminated `LabValue` union
 * (agent/app/models/w2/lab_values.py): `numeric` carries a float and an
 * optional comparator, `qualitative` carries text.
 *
 * Keeping the discriminator rather than flattening to a string is what
 * lets `toOpenEmrDataType()` be right. OpenEMR's FHIR read decides between
 * `valueQuantity` and `valueString` with `is_numeric()` on the stored
 * column (FhirObservationTrait.php:225), so `<5` necessarily surfaces as a
 * string; storing it as `result_data_type` `N` would claim a numeric
 * reading that neither OpenEMR nor the report supports.
 */
final readonly class LabValue
{
    public const KIND_NUMERIC = 'numeric';
    public const KIND_QUALITATIVE = 'qualitative';

    /** FHIR `Quantity.comparator`, which is where the agent's list comes from. */
    private const COMPARATORS = ['<', '<=', '>', '>='];

    private function __construct(
        public string $kind,
        public ?float $value,
        public ?string $comparator,
        public ?string $text,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function fromArray(array $payload, string $field): self
    {
        $kind = RequestField::string($payload, 'kind', $field . '.kind');
        if ($kind === self::KIND_NUMERIC) {
            RequestField::rejectUnknownKeys($payload, ['kind', 'value', 'comparator'], $field);
            $comparator = RequestField::optionalString($payload, 'comparator', $field . '.comparator');
            if ($comparator !== null && !in_array($comparator, self::COMPARATORS, true)) {
                throw new InvalidWriteBackRequest(
                    $field . '.comparator',
                    'is one of ' . implode(' ', self::COMPARATORS) . ' when present'
                );
            }
            $value = RequestField::optionalFloat($payload, 'value', $field . '.value');
            if ($value === null) {
                throw new InvalidWriteBackRequest($field . '.value', 'is required and is a finite number');
            }
            return new self(self::KIND_NUMERIC, $value, $comparator, null);
        }
        if ($kind === self::KIND_QUALITATIVE) {
            RequestField::rejectUnknownKeys($payload, ['kind', 'text'], $field);
            return new self(
                self::KIND_QUALITATIVE,
                null,
                null,
                RequestField::string($payload, 'text', $field . '.text')
            );
        }
        throw new InvalidWriteBackRequest(
            $field . '.kind',
            'is "' . self::KIND_NUMERIC . '" or "' . self::KIND_QUALITATIVE . '"'
        );
    }

    /** `procedure_result.result` — the value as a report writes it. */
    public function toOpenEmrResult(): string
    {
        if ($this->kind === self::KIND_QUALITATIVE) {
            return (string) $this->text;
        }
        $number = rtrim(rtrim(number_format((float) $this->value, 6, '.', ''), '0'), '.');
        if ($number === '' || $number === '-') {
            $number = '0';
        }
        return ($this->comparator ?? '') . $number;
    }

    /**
     * `procedure_result.result_data_type` — `N` only for a value OpenEMR
     * will read back as a quantity. See the class docstring.
     */
    public function toOpenEmrDataType(): string
    {
        return ($this->kind === self::KIND_NUMERIC && $this->comparator === null) ? 'N' : 'S';
    }

    /**
     * Is `$stored` — `procedure_result.result` as some source wrote it —
     * this value?
     *
     * D24: numbers are never near-matched. A numeric value matches a stored
     * numeric with the same comparator and the same number (`4.10` and
     * `4.1` are one number, `4.1` and `4.2` are two); a censored value never
     * matches an exact one. A qualitative value matches the same term,
     * ignoring case and surrounding or repeated whitespace only.
     */
    public function matches(string $stored): bool
    {
        $stored = trim($stored);
        if ($this->kind === self::KIND_QUALITATIVE) {
            return self::normaliseText((string) $this->text) === self::normaliseText($stored);
        }
        if (preg_match('/^(<=|>=|<|>)?\s*([-+]?(?:\d+\.?\d*|\.\d+))$/', $stored, $parts) !== 1) {
            return false;
        }
        $comparator = $parts[1] === '' ? null : $parts[1];
        return $comparator === $this->comparator && (float) $parts[2] === (float) $this->value;
    }

    /**
     * Is `$stored` — `procedure_result.units` — the unit of this row? Case
     * and whitespace are spelling; nothing else is (`mg/dL` and `mmol/L`
     * are different units, and a row with no unit is not a row with one).
     */
    public static function sameUnit(?string $unit, string $stored): bool
    {
        return self::normaliseText($unit ?? '') === self::normaliseText($stored);
    }

    private static function normaliseText(string $text): string
    {
        return strtolower(trim((string) preg_replace('/\s+/', ' ', $text)));
    }
}
