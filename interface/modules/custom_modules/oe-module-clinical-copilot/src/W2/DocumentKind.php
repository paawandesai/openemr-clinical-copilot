<?php

/**
 * Which schema an uploaded document is extracted into
 * (agent/app/models/w2/documents.py `DocumentKind`), and where the module
 * files it in the chart.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

use OpenEMR\Modules\ClinicalCopilot\ClinicianTokenBroker;

enum DocumentKind: string
{
    case LabPdf = 'lab_pdf';
    case IntakeForm = 'intake_form';

    /**
     * The document category it is stored under. Both are `patients|docs`
     * categories on a stock install, the ACL the card and the preview check.
     * A lab report goes where the write-back files one
     * (WriteBack\WriteBackDocument::DEFAULT_CATEGORY_PATH), so a report
     * uploaded here and later written back is one document, not two.
     */
    public function categoryPath(): string
    {
        return match ($this) {
            self::LabPdf => '/Categories/Lab_Report',
            self::IntakeForm => '/Categories/Medical_Record',
        };
    }

    /**
     * The scopes the upload's token asks for. A lab report's rows are
     * written back after extraction (D54), so its token carries the write
     * scopes; an intake form's never does (D2).
     */
    public function uploadScopes(): string
    {
        return match ($this) {
            self::LabPdf => ClinicianTokenBroker::LAB_UPLOAD_SCOPES,
            self::IntakeForm => ClinicianTokenBroker::SCOPES,
        };
    }
}
