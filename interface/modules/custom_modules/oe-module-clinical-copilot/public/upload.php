<?php

/**
 * POST endpoint behind the Documents & Sources card's upload control: hands a
 * PDF to the agent, which starts extracting it at once (D7), and files the
 * same bytes in the open patient's chart. docs/w2/ui-integration.md §2.2.
 *
 * Order, and why:
 *  1. ChartRequest — POST, CSRF, pid, the documents ACL at write level (this
 *     attaches a file to the chart), and pid = the session's open patient.
 *  2. The form: correlation id, document kind, one PDF of at most 25 MB.
 *  3. A file this module already filed on another chart is refused, 409,
 *     before the agent is asked (D40 item 8; ChartDocuments).
 *  4. The agent, with the clinician's token and the open chart's Patient
 *     uuid. It decides the 409 too, and starts the extraction. A lab
 *     report's token also carries the write-back scopes: the agent writes
 *     its rows to the chart when the extraction completes (D54).
 *  5. Only after the agent accepted: the chart copy, idempotent on the
 *     SHA3-512 digest (invariant 6). Storing first would leave a document on
 *     this chart that the agent refused for it.
 *
 * The token, the client secret and the file name never reach the agent or a
 * log; the file name is kept only as the chart document's name, as OpenEMR's
 * own upload keeps it.
 *
 * Request (multipart): pid, csrf_token_form, correlation_id, document_kind
 *   ("lab_pdf" | "intake_form"), document (the file).
 * Response: {"status": "ok", document_id, kind, extraction_status,
 *   byte_length, correlation_id}, or the error envelope with
 *   BriefFailure::httpStatus() (413 for an oversize file).
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
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;
use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\ChartRequest;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\CorrelationId;
use OpenEMR\Modules\ClinicalCopilot\ModuleConfig;
use OpenEMR\Modules\ClinicalCopilot\ModuleNotConfigured;
use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
use OpenEMR\Modules\ClinicalCopilot\W2\AgentDocumentId;
use OpenEMR\Modules\ClinicalCopilot\W2\CardServices;
use OpenEMR\Modules\ClinicalCopilot\W2\ChartDocuments;
use OpenEMR\Modules\ClinicalCopilot\W2\DocumentKind;
use OpenEMR\Modules\ClinicalCopilot\W2\QueryUtilsChartDocumentIndex;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentStore;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\SourceDocumentReader;
use OpenEMR\Services\DocumentService;
use OpenEMR\Services\PatientService;
use Symfony\Component\HttpFoundation\File\UploadedFile;
use Symfony\Component\HttpFoundation\Request;

$globals = OEGlobalsBag::getInstance();
$session = SessionWrapperFactory::getInstance()->getActiveSession();
$logger = ServiceContainer::getLogger();
$request = Request::createFromGlobals();

/**
 * @param array<string, mixed> $payload
 */
$respond = static function (int $status, array $payload): never {
    http_response_code($status);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    echo json_encode($payload, JSON_THROW_ON_ERROR);
    exit;
};

$fail = static function (BriefFailure $failure, ?string $correlationId = null, ?int $status = null) use ($respond): never {
    $respond($status ?? $failure->httpStatus(), [
        'status' => 'error',
        'error' => $failure->value,
        'message' => xl($failure->message()),
        'correlation_id' => $correlationId,
    ]);
};

try {
    $chart = ChartRequest::fromRequest(
        $request,
        $session->get('pid'),
        static fn (string $token): bool => CsrfUtils::verifyCsrfToken($token, $session),
        // Attaching a file to the chart is a write: the ACL OpenEMR's own
        // document upload asks for.
        static fn (): bool => AclMain::aclCheckCore(Bootstrap::DOCUMENTS_ACL[0], Bootstrap::DOCUMENTS_ACL[1], '', ['write', 'addonly']),
    );
} catch (BriefUnavailable $e) {
    $fail($e->failure);
}

try {
    $correlation = new CorrelationId($request->request->getString('correlation_id'));
} catch (\DomainException) {
    $fail(BriefFailure::InvalidRequest);
}
$trace = $correlation->value;

$kind = DocumentKind::tryFrom($request->request->getString('document_kind'));
if ($kind === null) {
    $fail(BriefFailure::UploadRejected, $trace);
}
$file = $request->files->get('document');
if (!$file instanceof UploadedFile) {
    $fail(BriefFailure::UploadRejected, $trace);
}
if (in_array($file->getError(), [UPLOAD_ERR_INI_SIZE, UPLOAD_ERR_FORM_SIZE], true)) {
    $fail(BriefFailure::UploadRejected, $trace, 413);
}
if (!$file->isValid()) {
    $fail(BriefFailure::UploadRejected, $trace);
}
if ($file->getSize() > ChartDocuments::MAX_UPLOAD_BYTES) {
    $fail(BriefFailure::UploadRejected, $trace, 413);
}
$pdf = file_get_contents($file->getPathname());
if (!is_string($pdf) || !str_starts_with($pdf, '%PDF-')) {
    $fail(BriefFailure::UploadRejected, $trace);
}
if (strlen($pdf) > ChartDocuments::MAX_UPLOAD_BYTES) {
    $fail(BriefFailure::UploadRejected, $trace, 413);
}
$digest = AgentDocumentId::ofBytes($pdf);

try {
    $config = ModuleConfig::fromGlobals($globals, ServiceContainer::getCrypto());
} catch (ModuleNotConfigured $e) {
    $logger->warning('Clinical Co-Pilot upload requested but the module is not configured', ['setting' => $e->setting]);
    $fail(BriefFailure::NotConfigured, $trace);
}

try {
    $openPatient = OpenPatient::fromPatientData((new PatientService())->findByPid($chart->pid));
    $patient = $openPatient->uuid;
    $coreSession = new CoreSessionId($session->getId());
} catch (\DomainException $e) {
    $logger->error('Clinical Co-Pilot could not identify the patient or session for an upload', ['exception' => $e]);
    $fail(BriefFailure::InvalidRequest, $trace);
}

// The co-pilot's own access record (AUDIT.md F-52). Identifiers and
// outcome only: never the file name, never anything read from the file.
$audit = static function (bool $success, string $outcome) use ($session, $chart, $digest, $trace): void {
    EventAuditLogger::getInstance()->newEvent(
        'patient-record',
        $session->get('authUser'),
        $session->get('authProvider'),
        $success ? 1 : 0,
        'Clinical Co-Pilot document upload: ' . $outcome . ' (document ' . substr($digest->value, 0, 16)
            . ', correlation_id ' . $trace . ')',
        $chart->pid,
    );
};

$documents = new ChartDocuments(
    new DocumentStore(new DocumentService(), $globals, $logger),
    new QueryUtilsChartDocumentIndex(),
    $logger,
);

try {
    $documents->assertNotFiledElsewhere($chart->pid, $digest);
    $broker = CardServices::broker($config, $globals->getWebRoot(), $session->get('site_id'), $logger);
    $state = CardServices::agent($config, $logger, $correlation)
        ->uploadDocument($broker->issueFor($coreSession, $patient, $kind->uploadScopes()), $patient, $kind, $pdf);
    $owner = filter_var($session->get('authUserID'), FILTER_VALIDATE_INT);
    $filed = $documents->attach(
        $chart->pid,
        $kind,
        $digest,
        SourceDocumentReader::safeFilename($file->getClientOriginalName()),
        $pdf,
        $owner === false ? 0 : $owner,
    );
} catch (BriefUnavailable $e) {
    $audit(false, $e->failure->value);
    $fail($e->failure, $e->correlationId ?? $trace);
}

$audit(true, $state->extractionStatus . ', documents.id ' . $filed->documentId . ($filed->created ? ' stored' : ' already on chart'));
$respond(200, $state->toUploadPayload());
