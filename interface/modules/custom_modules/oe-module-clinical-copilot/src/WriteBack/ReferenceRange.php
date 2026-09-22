<?php

/**
 * The interval a lab result is judged against.
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
 * Mirrors the agent-side `ReferenceRange`
 * (agent/app/models/w2/lab_values.py): `low`/`high` are the comparable
 * form, `text` is what the report actually printed.
 */
final readonly class ReferenceRange
{
    public function __construct(
        public ?float $low = null,
        public ?float $high = null,
        public ?string $text = null,
        public ?string $unit = null,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function fromArray(array $payload, string $field): self
    {
        RequestField::rejectUnknownKeys($payload, ['low', 'high', 'text', 'unit'], $field);
        $range = new self(
            RequestField::optionalFloat($payload, 'low', $field . '.low'),
            RequestField::optionalFloat($payload, 'high', $field . '.high'),
            RequestField::optionalString($payload, 'text', $field . '.text'),
            RequestField::optionalString($payload, 'unit', $field . '.unit'),
        );
        if ($range->low === null && $range->high === null && $range->text === null) {
            throw new InvalidWriteBackRequest(
                $field,
                'a reference range states something: low, high or text'
            );
        }
        if ($range->low !== null && $range->high !== null && $range->low > $range->high) {
            throw new InvalidWriteBackRequest($field, 'reference range low is above high');
        }
        return $range;
    }

    /**
     * `procedure_result.range`, in the one shape OpenEMR's FHIR read can
     * parse back into `Observation.referenceRange`.
     *
     * FhirObservationLaboratoryService splits this column on `-` and emits
     * a referenceRange only when both halves parse as floats
     * (FhirObservationLaboratoryService.php:266-275), so `low - high` is
     * the only form that round-trips. With one bound or none, the printed
     * text is stored instead: it is still shown to a clinician in the
     * Procedure Results screen, it is simply not machine-readable over
     * FHIR, and inventing the missing bound to make it so would put a
     * number in the chart that no laboratory printed.
     */
    public function toOpenEmrRange(): string
    {
        if ($this->low !== null && $this->high !== null) {
            return $this->formatBound($this->low) . '-' . $this->formatBound($this->high);
        }
        return $this->text ?? '';
    }

    private function formatBound(float $bound): string
    {
        return rtrim(rtrim(number_format($bound, 4, '.', ''), '0'), '.') ?: '0';
    }
}
