<?php

/**
 * POST endpoint behind the Pre-Visit Brief card: obtains a token for the
 * logged-in clinician, asks the agent for the brief of the patient whose
 * chart is open, and returns what the card shows as JSON.
 *
 * The token, the client secret and the session id never reach the browser.
 *
 * Request (form-encoded): pid, csrf_token_form.
 * Response: PreVisitBrief::toCardPayload(), which names the open patient, or
 *   {"status": "error", "error": BriefFailure value, "message": ..., "correlation_id": ...|null}
 *   with BriefFailure::httpStatus().
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
use OpenEMR\Modules\ClinicalCopilot\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;
use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\ChartRequest;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\ModuleConfig;
use OpenEMR\Modules\ClinicalCopilot\ModuleNotConfigured;
use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
use OpenEMR\Modules\ClinicalCopilot\W2\CardServices;
use OpenEMR\Services\PatientService;
use Symfony\Component\HttpClient\HttpClient;
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
        static fn (): bool => AclMain::aclCheckCore(Bootstrap::ACL[0], Bootstrap::ACL[1]),
    );
} catch (BriefUnavailable $e) {
    $fail($e->failure);
}
$requestedPid = $chart->pid;

try {
    $config = ModuleConfig::fromGlobals($globals, ServiceContainer::getCrypto());
} catch (ModuleNotConfigured $e) {
    $logger->warning('Clinical Co-Pilot brief requested but the module is not configured', ['setting' => $e->setting]);
    $fail(BriefFailure::NotConfigured);
}

try {
    $openPatient = OpenPatient::fromPatientData((new PatientService())->findByPid($requestedPid));
    $patient = $openPatient->uuid;
    $coreSession = new CoreSessionId($session->getId());
} catch (\DomainException $e) {
    $logger->error('Clinical Co-Pilot could not identify the patient or session for a brief', ['exception' => $e]);
    $fail(BriefFailure::InvalidRequest);
}

$broker = CardServices::broker($config, $globals->getWebRoot(), $session->get('site_id'), $session->get('authUserID'), $logger);
$agent = new AgentClient(
    HttpClient::create($config->agentCaFile === null ? [] : ['cafile' => $config->agentCaFile]),
    $logger,
    $config->agentUrl,
);

// The co-pilot's own access record (AUDIT.md F-52: no core event fires when
// the FHIR reads it causes happen). No clinical content in the comment.
$audit = static function (bool $success, string $outcome, ?string $correlationId) use ($session, $requestedPid): void {
    EventAuditLogger::getInstance()->newEvent(
        'patient-record',
        $session->get('authUser'),
        $session->get('authProvider'),
        $success ? 1 : 0,
        'Clinical Co-Pilot Pre-Visit Brief: ' . $outcome . ($correlationId === null ? '' : ' (correlation_id ' . $correlationId . ')'),
        $requestedPid,
    );
};

try {
    $brief = $agent->preVisitBrief($broker->issueFor($coreSession, $patient), $patient);
} catch (BriefUnavailable $e) {
    $audit(false, $e->failure->value, $e->correlationId);
    $fail($e->failure, $e->correlationId);
}

$audit(true, 'shown', $brief->correlationId);
$respond(200, $brief->toCardPayload($openPatient));
