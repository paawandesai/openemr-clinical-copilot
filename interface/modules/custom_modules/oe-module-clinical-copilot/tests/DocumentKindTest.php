<?php

/**
 * Which scopes an upload's token asks for: the write-back's for a lab
 * report (D54), never for an intake form (D2).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests;

use OpenEMR\Modules\ClinicalCopilot\ClinicianTokenBroker;
use OpenEMR\Modules\ClinicalCopilot\W2\DocumentKind;
use PHPUnit\Framework\TestCase;

final class DocumentKindTest extends TestCase
{
    public function testALabReportUploadCarriesTheWriteBackScopesOnTopOfTheReadScopes(): void
    {
        $scopes = explode(' ', DocumentKind::LabPdf->uploadScopes());

        $this->assertContains('api:oemr', $scopes);
        $this->assertContains('user/lab-writeback.c', $scopes);
        foreach (explode(' ', ClinicianTokenBroker::SCOPES) as $read) {
            $this->assertContains($read, $scopes);
        }
    }

    public function testAnIntakeFormUploadNeverAsksToWrite(): void
    {
        $scopes = DocumentKind::IntakeForm->uploadScopes();

        $this->assertSame(ClinicianTokenBroker::SCOPES, $scopes);
        $this->assertStringNotContainsString('lab-writeback', $scopes);
        $this->assertStringNotContainsString('api:oemr', $scopes);
    }
}
