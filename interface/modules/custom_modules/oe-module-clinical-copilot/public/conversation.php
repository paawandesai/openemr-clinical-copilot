<?php

/**
 * POST endpoint behind the follow-up conversation in the Pre-Visit Brief
 * card: one verified turn about the patient whose chart is open, or the end
 * of a conversation. Same token flow and same request checks as brief.php
 * (ChartRequest).
 *
 * The token, the client secret, the login session id and the agent's
 * conversation session id never reach the browser. The browser names a
 * conversation with a nonce only; the agent's session id is derived here from
 * the login session, the open patient and that nonce
 * (ConversationSessionId), so a conversation cannot follow the clinician to
 * another patient's chart.
 *
 * Request (form-encoded): pid, csrf_token_form, conversation, action
 *   ("ask", the default, with question; or "end").
 * Response: ConversationTurn::toCardPayload(), which names the open patient, or {"status": "ended"}, or
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
use OpenEMR\FHIR\Config\ServerConfig;
use OpenEMR\Modules\ClinicalCopilot\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;
use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\ChartRequest;
use OpenEMR\Modules\ClinicalCopilot\ClinicianTokenBroker;
use OpenEMR\Modules\ClinicalCopilot\ConversationNonce;
use OpenEMR\Modules\ClinicalCopilot\ConversationSessionId;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\FollowUpQuestion;
use OpenEMR\Modules\ClinicalCopilot\ModuleConfig;
use OpenEMR\Modules\ClinicalCopilot\ModuleNotConfigured;
use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
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

$action = $request->request->getString('action', 'ask');
if (!in_array($action, ['ask', 'end'], true)) {
    $fail(BriefFailure::InvalidRequest);
}
try {
    $nonce = new ConversationNonce($request->request->getString('conversation'));
} catch (\DomainException) {
    $fail(BriefFailure::InvalidRequest);
}
$question = null;
if ($action === 'ask') {
    try {
        $question = new FollowUpQuestion($request->request->getString('question'));
    } catch (\DomainException) {
        $fail(BriefFailure::InvalidQuestion);
    }
}

try {
    $config = ModuleConfig::fromGlobals($globals, ServiceContainer::getCrypto());
} catch (ModuleNotConfigured $e) {
    $logger->warning('Clinical Co-Pilot follow-up requested but the module is not configured', ['setting' => $e->setting]);
    $fail(BriefFailure::NotConfigured);
}

// The patient sent to the agent, and the name the card puts above the answer,
// both come from the pid ChartRequest has just matched to the session: the
// open chart's patient, on every turn.
try {
    $openPatient = OpenPatient::fromPatientData((new PatientService())->findByPid($chart->pid));
    $patient = $openPatient->uuid;
    $coreSession = new CoreSessionId($session->getId());
} catch (\DomainException $e) {
    $logger->error('Clinical Co-Pilot could not identify the patient or session for a follow-up', ['exception' => $e]);
    $fail(BriefFailure::InvalidRequest);
}
$conversation = ConversationSessionId::derive($coreSession, $patient, $nonce);

$siteId = $session->get('site_id');
$broker = new ClinicianTokenBroker(
    HttpClient::create(),
    $logger,
    ClinicianTokenBroker::smartLaunchToken(...),
    $config->loopbackUrl . $globals->getWebRoot() . '/oauth2/' . rawurlencode(is_string($siteId) && $siteId !== '' ? $siteId : 'default'),
    (new ServerConfig())->getFhirUrl(),
    $config->clientId,
    $config->clientSecret,
    $config->redirectUri,
);
$agent = new AgentClient(
    HttpClient::create($config->agentCaFile === null ? [] : ['cafile' => $config->agentCaFile]),
    $logger,
    $config->agentUrl,
);

if ($question === null) {
    // Best effort: the agent discards an idle conversation on its own.
    try {
        $agent->endConversation($broker->issueFor($coreSession, $patient), $patient, $conversation);
    } catch (BriefUnavailable $e) {
        $fail($e->failure, $e->correlationId);
    }
    $respond(200, ['status' => 'ended']);
}

// The co-pilot's own access record, as for the brief (AUDIT.md F-52). The
// comment carries the outcome and correlation id, never the question or answer.
$audit = static function (bool $success, string $outcome, ?string $correlationId) use ($session, $chart): void {
    EventAuditLogger::getInstance()->newEvent(
        'patient-record',
        $session->get('authUser'),
        $session->get('authProvider'),
        $success ? 1 : 0,
        'Clinical Co-Pilot follow-up question: ' . $outcome . ($correlationId === null ? '' : ' (correlation_id ' . $correlationId . ')'),
        $chart->pid,
    );
};

try {
    $turn = $agent->conversationTurn(
        $broker->issueFor($coreSession, $patient, ClinicianTokenBroker::CONVERSATION_SCOPES),
        $patient,
        $conversation,
        $question,
    );
} catch (BriefUnavailable $e) {
    $audit(false, $e->failure->value, $e->correlationId);
    $fail($e->failure, $e->correlationId);
}

$audit(true, $turn->turnStatus, $turn->correlationId);
$respond(200, $turn->toCardPayload($openPatient));
