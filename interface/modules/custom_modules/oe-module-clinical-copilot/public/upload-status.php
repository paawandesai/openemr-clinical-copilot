<?php

/**
 * GET endpoint the upload control polls: how far a document's extraction has
 * got. docs/w2/ui-integration.md §2.3.
 *
 * A GET because the browser repeats it, checked exactly as the POST routes
 * are (ChartRequest with the method GET): CSRF, pid, the documents ACL, pid =
 * the session's open patient. "Not finished" is a 200 — `pending` and
 * `in_progress` are successful reads of a job still running (D7).
 *
 * Not written to the audit log: a poll repeats every few seconds and reads a
 * status, not the record. The upload that started the job and the question
 * that reads its results are each logged.
 *
 * QUARANTINE (D60). The poll is where the card learns the extraction ended,
 * so it is where a wrong-patient document is dealt with: when the agent
 * reports `identity_check: mismatch`, the chart copy is moved to the
 * "Quarantine - Patient Mismatch" category with a note saying why
 * (ChartDocuments::quarantine), and that move — the one write this route can
 * make — is audited. It needs the documents ACL at write level, as the
 * upload did; a poll without it reports `failed` and moves nothing. The move
 * is keyed on the digest, so later polls change nothing.
 *
 * Request (query): pid, csrf_token_form, correlation_id, document_id.
 * Response: {"status": "ok", document_id, kind, extraction_status,
 *   extraction_id, failure_reason, correlation_id, identity_check,
 *   quarantine}, or the error envelope.
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
use OpenEMR\Modules\ClinicalCopilot\W2\QuarantineOutcome;
use OpenEMR\Modules\ClinicalCopilot\W2\QueryUtilsChartDocumentIndex;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentStore;
use OpenEMR\Services\DocumentService;
use OpenEMR\Services\PatientService;
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

$fail = static function (BriefFailure $failure, ?string $correlationId = null) use ($respond): never {
    $respond($failure->httpStatus(), [
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
        static fn (): bool => AclMain::aclCheckCore(Bootstrap::DOCUMENTS_ACL[0], Bootstrap::DOCUMENTS_ACL[1]),
        'GET',
    );
} catch (BriefUnavailable $e) {
    $fail($e->failure);
}

try {
    $correlation = new CorrelationId($request->query->getString('correlation_id'));
    $document = new AgentDocumentId($request->query->getString('document_id'));
} catch (\DomainException) {
    $fail(BriefFailure::InvalidRequest);
}
$trace = $correlation->value;

try {
    $config = ModuleConfig::fromGlobals($globals, ServiceContainer::getCrypto());
} catch (ModuleNotConfigured $e) {
    $logger->warning('Clinical Co-Pilot extraction status requested but the module is not configured', ['setting' => $e->setting]);
    $fail(BriefFailure::NotConfigured, $trace);
}

try {
    $patient = OpenPatient::fromPatientData((new PatientService())->findByPid($chart->pid))->uuid;
    $coreSession = new CoreSessionId($session->getId());
} catch (\DomainException $e) {
    $logger->error('Clinical Co-Pilot could not identify the patient or session for a status poll', ['exception' => $e]);
    $fail(BriefFailure::InvalidRequest, $trace);
}

try {
    $broker = CardServices::broker($config, $globals->getWebRoot(), $session->get('site_id'), $logger);
    $state = CardServices::agent($config, $logger, $correlation)
        ->extractionState($broker->issueFor($coreSession, $patient), $patient, $document);
} catch (BriefUnavailable $e) {
    $fail($e->failure, $e->correlationId ?? $trace);
}

$quarantine = null;
if ($state->isIdentityMismatch()) {
    if (!AclMain::aclCheckCore(Bootstrap::DOCUMENTS_ACL[0], Bootstrap::DOCUMENTS_ACL[1], '', ['write', 'addonly'])) {
        $quarantine = QuarantineOutcome::Failed;
    } else {
        $owner = filter_var($session->get('authUserID'), FILTER_VALIDATE_INT);
        $result = (new ChartDocuments(
            new DocumentStore(new DocumentService(), $globals, $logger),
            new QueryUtilsChartDocumentIndex(),
            $logger,
        ))->quarantine($chart->pid, $document, $trace, $owner === false ? 0 : $owner);
        $quarantine = $result->outcome;
        if ($result->moved) {
            // The co-pilot's own access record (AUDIT.md F-52): identifiers only.
            EventAuditLogger::getInstance()->newEvent(
                'patient-record',
                $session->get('authUser'),
                $session->get('authProvider'),
                1,
                'Clinical Co-Pilot document quarantined: identity mismatch (documents.id ' . $result->documentId
                    . ', document ' . substr($document->value, 0, 16) . ', correlation_id ' . $trace . ')',
                $chart->pid,
            );
        }
    }
}

$respond(200, $state->toStatusPayload($quarantine));
