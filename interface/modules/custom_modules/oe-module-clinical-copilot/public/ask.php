<?php

/**
 * POST endpoint behind the Documents & Sources card's question box: one turn
 * of the Week 2 ask path (agent POST /w2/ask, docs/w2/ui-integration.md §2.4)
 * about the patient whose chart is open, optionally about documents uploaded
 * to that chart.
 *
 * Same checks and same token flow as conversation.php, and the same
 * conversation rule: the browser names its conversation with a nonce, and the
 * agent's session id is derived here from the login session, the open
 * patient and that nonce (ConversationSessionId), so a conversation cannot
 * follow the clinician to another chart. The question reads the medical
 * record and may read documents, so both ACLs are required.
 *
 * Every clinical outcome — an answer, a refusal, a document still
 * processing — is a 200 from the agent and a 200 here, with the outcome in
 * `answer_status` and `reason_code`. The envelope's `status` stays "ok".
 *
 * Request (form-encoded): pid, csrf_token_form, correlation_id, conversation,
 *   question, document_ids[] (at most 4, as upload.php returned them).
 * Response: W2\AskAnswer::toCardPayload(), which names the open patient, or
 *   the error envelope with BriefFailure::httpStatus().
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
use OpenEMR\Modules\ClinicalCopilot\ClinicianTokenBroker;
use OpenEMR\Modules\ClinicalCopilot\ConversationNonce;
use OpenEMR\Modules\ClinicalCopilot\ConversationSessionId;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\CorrelationId;
use OpenEMR\Modules\ClinicalCopilot\FollowUpQuestion;
use OpenEMR\Modules\ClinicalCopilot\ModuleConfig;
use OpenEMR\Modules\ClinicalCopilot\ModuleNotConfigured;
use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
use OpenEMR\Modules\ClinicalCopilot\W2\AgentDocumentId;
use OpenEMR\Modules\ClinicalCopilot\W2\AskAnswer;
use OpenEMR\Modules\ClinicalCopilot\W2\CardServices;
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
        static fn (): bool => AclMain::aclCheckCore(Bootstrap::ACL[0], Bootstrap::ACL[1])
            && AclMain::aclCheckCore(Bootstrap::DOCUMENTS_ACL[0], Bootstrap::DOCUMENTS_ACL[1]),
    );
} catch (BriefUnavailable $e) {
    $fail($e->failure);
}

try {
    $correlation = new CorrelationId($request->request->getString('correlation_id'));
    $nonce = new ConversationNonce($request->request->getString('conversation'));
    $documents = AgentDocumentId::listFrom($request->request->all()['document_ids'] ?? null, AskAnswer::MAX_DOCUMENTS);
} catch (\DomainException) {
    $fail(BriefFailure::InvalidRequest);
}
$trace = $correlation->value;
try {
    $question = new FollowUpQuestion($request->request->getString('question'));
} catch (\DomainException) {
    $fail(BriefFailure::InvalidQuestion, $trace);
}

try {
    $config = ModuleConfig::fromGlobals($globals, ServiceContainer::getCrypto());
} catch (ModuleNotConfigured $e) {
    $logger->warning('Clinical Co-Pilot question requested but the module is not configured', ['setting' => $e->setting]);
    $fail(BriefFailure::NotConfigured, $trace);
}

// The patient sent to the agent, and the name the card puts above the answer,
// both come from the pid ChartRequest has just matched to the session.
try {
    $openPatient = OpenPatient::fromPatientData((new PatientService())->findByPid($chart->pid));
    $patient = $openPatient->uuid;
    $coreSession = new CoreSessionId($session->getId());
} catch (\DomainException $e) {
    $logger->error('Clinical Co-Pilot could not identify the patient or session for a question', ['exception' => $e]);
    $fail(BriefFailure::InvalidRequest, $trace);
}
$conversation = ConversationSessionId::derive($coreSession, $patient, $nonce);

// The co-pilot's own access record (AUDIT.md F-52): outcome, counts and
// correlation id, never the question or the answer.
$audit = static function (bool $success, string $outcome) use ($session, $chart, $documents, $trace): void {
    EventAuditLogger::getInstance()->newEvent(
        'patient-record',
        $session->get('authUser'),
        $session->get('authProvider'),
        $success ? 1 : 0,
        'Clinical Co-Pilot sourced question: ' . $outcome . ' (' . count($documents) . ' documents, correlation_id '
            . $trace . ')',
        $chart->pid,
    );
};

try {
    $broker = CardServices::broker($config, $globals->getWebRoot(), $session->get('site_id'), $session->get('authUserID'), $logger);
    $answer = CardServices::agent($config, $logger, $correlation)->ask(
        $broker->issueFor($coreSession, $patient, ClinicianTokenBroker::CONVERSATION_SCOPES),
        $patient,
        $conversation,
        $question,
        $documents,
    );
} catch (BriefUnavailable $e) {
    $audit(false, $e->failure->value);
    $fail($e->failure, $e->correlationId ?? $trace);
}

$audit(true, $answer->answerStatus . ' / ' . $answer->reasonCode);
$respond(200, $answer->toCardPayload($openPatient));
