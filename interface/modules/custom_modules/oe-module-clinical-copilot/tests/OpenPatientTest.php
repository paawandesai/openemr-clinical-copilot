<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests;

use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class OpenPatientTest extends TestCase
{
    private const UUID = 'a2c36e54-af42-4c38-b86e-ade616ed5494';

    public function testNamesThePatientAndCarriesTheUuidSentToTheAgentFromOneRow(): void
    {
        $patient = OpenPatient::fromPatientData(self::row());

        $this->assertSame(self::UUID, $patient->uuid->value);
        $this->assertSame(['name' => 'Phil Belford', 'date_of_birth' => '1972-02-09'], $patient->toCardPayload());
    }

    public function testIncludesAMiddleNameAndTrimsTheParts(): void
    {
        $patient = OpenPatient::fromPatientData(['fname' => ' Susan ', 'mname' => 'Ardmore', 'lname' => 'Underwood'] + self::row());

        $this->assertSame('Susan Ardmore Underwood', $patient->name);
    }

    #[DataProvider('unusableDatesOfBirth')]
    public function testADateOfBirthThatIsNotADateIsLeftOut(mixed $dob): void
    {
        $this->assertNull(OpenPatient::fromPatientData(['DOB' => $dob] + self::row())->dateOfBirth);
    }

    /**
     * @return array<string, array{mixed}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function unusableDatesOfBirth(): array
    {
        return ['null' => [null], 'empty' => [''], 'zero date' => ['0000-00-00'], 'free text' => ['unknown']];
    }

    /**
     * No name means no header, and an unnamed answer is what F-62 rules out:
     * the request fails instead.
     */
    #[DataProvider('unidentifiedPatients')]
    public function testRefusesARowThatDoesNotIdentifyThePatient(mixed $row): void
    {
        $this->expectException(\DomainException::class);
        OpenPatient::fromPatientData($row);
    }

    /**
     * @return array<string, array{mixed}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function unidentifiedPatients(): array
    {
        return [
            'no such pid' => [false],
            'no row' => [null],
            'no name' => [['fname' => '', 'mname' => null, 'lname' => ' '] + self::row()],
            'no uuid' => [['uuid' => ''] + self::row()],
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function row(): array
    {
        return [
            'pid' => '1',
            'uuid' => hex2bin(str_replace('-', '', self::UUID)),
            'fname' => 'Phil',
            'mname' => '',
            'lname' => 'Belford',
            'DOB' => '1972-02-09',
        ];
    }
}
