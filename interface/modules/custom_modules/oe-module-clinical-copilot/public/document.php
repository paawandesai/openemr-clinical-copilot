<?php

/**
 * GET endpoint behind the source-document preview: streams a lab PDF the
 * co-pilot wrote back, for the patient whose chart is open.
 *
 * Replaces `GET /fhir/Binary/:id` for clinicians, which requires the
 * `admin`/`users` ACL and therefore 403s for a physician — see
 * WriteBack\SourceDocumentReader and DECISIONS.md D24.
 *
 * Request (query): document_id.
 * Response: the PDF with `Content-Type: application/pdf`, or JSON
 *   {"status": "error", "error": DocumentFailure value, "message": ...}
 *   with DocumentFailure::httpStatus().
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

// Authenticates the session, and refuses this script if the module is disabled.
require_once dirname(__DIR__, 4) . '/globals.php';

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentFailure;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentReadRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentUnavailable;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\SourceDocumentReader;
use Symfony\Component\HttpFoundation\Request;

$session = SessionWrapperFactory::getInstance()->getActiveSession();
$logger = ServiceContainer::getLogger();
$request = Request::createFromGlobals();

$fail = static function (DocumentFailure $failure): never {
    http_response_code($failure->httpStatus());
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    echo json_encode([
        'status' => 'error',
        'error' => $failure->value,
        'message' => xl($failure->message()),
    ], JSON_THROW_ON_ERROR);
    exit;
};

try {
    $read = DocumentReadRequest::fromRequest(
        $request,
        $session->get('pid'),
        // The ACL OpenEMR's own document viewer checks. Reading is enough:
        // no write or addonly permission is asked for.
        static fn (): bool => AclMain::aclCheckCore('patients', 'docs'),
    );
    $document = (new SourceDocumentReader($logger))->read($read);
} catch (DocumentUnavailable $e) {
    $fail($e->failure);
}

// Viewing a patient's document is an access event. Identifiers only.
EventAuditLogger::getInstance()->newEvent(
    'patient-record',
    $session->get('authUser'),
    $session->get('authProvider'),
    1,
    'Clinical Co-Pilot source document preview: documents.id ' . $read->documentId,
    $read->pid,
);

http_response_code(200);
header('Content-Type: ' . $document->mimeType);
header('Content-Length: ' . strlen($document->bytes));
// Inline so the card can embed it; the filename is sanitised because the
// stored name came from an upload.
header('Content-Disposition: inline; filename="' . $document->filename . '"');
// The body is chart content: never cached by a shared proxy, never sniffed
// into another type, never framed by another origin.
header('Cache-Control: no-store, private');
header('Pragma: no-cache');
header('X-Content-Type-Options: nosniff');
header('Content-Security-Policy: default-src \'none\'; object-src \'self\'; frame-ancestors \'self\'');
echo $document->bytes;
