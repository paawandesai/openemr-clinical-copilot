<?php

/**
 * The patient whose chart is open, as the card names them above every brief
 * and every follow-up answer (docs/audit/findings.md F-62).
 *
 * Built only from the patient_data row of the pid ChartRequest has matched
 * to the session, in the same request that sends this patient's UUID to the
 * agent. The name shown is therefore the patient the answer was computed
 * against; it never comes from the browser or from the question.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Common\Uuid\UuidRegistry;

final readonly class OpenPatient
{
    /**
     * @param ?string $dateOfBirth YYYY-MM-DD: a second identifier, for two patients with one name.
     */
    private function __construct(
        public PatientUuid $uuid,
        public string $name,
        public ?string $dateOfBirth,
    ) {
    }

    /**
     * @param mixed $row PatientService::findByPid(): a patient_data row, uuid in binary.
     * @throws \DomainException If the row does not identify a patient by UUID and name.
     */
    public static function fromPatientData(mixed $row): self
    {
        if (!is_array($row)) {
            throw new \DomainException('No patient_data row for the open patient');
        }
        $uuid = $row['uuid'] ?? null;
        $name = implode(' ', array_filter(
            [self::text($row['fname'] ?? null), self::text($row['mname'] ?? null), self::text($row['lname'] ?? null)],
            static fn (string $part): bool => $part !== '',
        ));
        if (!is_string($uuid) || $uuid === '' || $name === '') {
            throw new \DomainException('The open patient has no UUID or no name');
        }
        $dob = self::text($row['DOB'] ?? null);
        return new self(
            new PatientUuid(UuidRegistry::uuidToString($uuid)),
            $name,
            preg_match('/^\d{4}-\d{2}-\d{2}$/', $dob) === 1 && $dob !== '0000-00-00' ? $dob : null,
        );
    }

    /**
     * @return array{name: string, date_of_birth: ?string}
     */
    public function toCardPayload(): array
    {
        return ['name' => $this->name, 'date_of_birth' => $this->dateOfBirth];
    }

    private static function text(mixed $value): string
    {
        return is_string($value) ? trim($value) : '';
    }
}
