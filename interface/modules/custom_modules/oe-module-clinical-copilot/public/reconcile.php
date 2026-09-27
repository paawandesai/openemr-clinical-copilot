<?php

/**
 * POST endpoint the Documents & Sources card calls once when it loads: brings
 * the chart's quarantine up to date with the agent (D60).
 *
 * The status poll quarantines a wrong-patient upload when it sees the
 * extraction finish. If the page was closed first, nothing saw it, and the
 * copy stayed filed as a normal document. So on every card load the module
 * lists this chart's newest copies in its own two categories that are not
 * quarantined (ChartDocuments::RECONCILE_LIMIT of them), asks the agent
 * about each, and moves every one reported as `identity_check: mismatch`
 * into quarantine, with its note and an audit row, as the poll would have.
 *
 * Cheap when there is nothing to do: with no candidate copies no token is
 * minted and the agent is not called. A copy the agent does not know under
 * this chart (never uploaded through the card, or its row expired) is left
 * alone. Moving needs the documents ACL at write level, as the upload does;
 * without it the route reports `skipped` and moves nothing.
 *
 * Request (form): pid, csrf_token_form, correlation_id.
 * Response: {"status": "ok", checked, quarantined, failed, skipped,
 *   correlation_id}, or the error envelope.
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
use OpenEMR\Modules\ClinicalCopilot\W2\ExtractionState;
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

$done = static fn (array $counts, ?string $skipped = null): never => $respond(200, [
    'status' => 'ok',
    ...$counts,
    'skipped' => $skipped,
    'correlation_id' => $trace,
]);
$nothing = ['checked' => 0, 'quarantined' => 0, 'failed' => 0];

if (!AclMain::aclCheckCore(Bootstrap::DOCUMENTS_ACL[0], Bootstrap::DOCUMENTS_ACL[1], '', ['write', 'addonly'])) {
    $done($nothing, 'no_write_acl');
}

try {
    $config = ModuleConfig::fromGlobals($globals, ServiceContainer::getCrypto());
} catch (ModuleNotConfigured $e) {
    $logger->warning('Clinical Co-Pilot reconcile requested but the module is not configured', ['setting' => $e->setting]);
    $fail(BriefFailure::NotConfigured, $trace);
}

try {
    $patient = OpenPatient::fromPatientData((new PatientService())->findByPid($chart->pid))->uuid;
    $coreSession = new CoreSessionId($session->getId());
} catch (\DomainException $e) {
    $logger->error('Clinical Co-Pilot could not identify the patient or session for a reconcile', ['exception' => $e]);
    $fail(BriefFailure::InvalidRequest, $trace);
}

$documents = new ChartDocuments(
    new DocumentStore(new DocumentService(), $globals, $logger),
    new QueryUtilsChartDocumentIndex(),
    $logger,
);

// Built only when there are copies to ask about: one token for all of them.
$agent = static function () use ($config, $globals, $session, $logger, $correlation, $coreSession, $patient): \Closure {
    try {
        $broker = CardServices::broker($config, $globals->getWebRoot(), $session->get('site_id'), $session->get('authUserID'), $logger);
        $token = $broker->issueFor($coreSession, $patient);
    } catch (BriefUnavailable) {
        return static fn (AgentDocumentId $document): ?ExtractionState => null;
    }
    $client = CardServices::agent($config, $logger, $correlation);
    return static function (AgentDocumentId $document) use ($client, $token, $patient): ?ExtractionState {
        try {
            return $client->extractionState($token, $patient, $document);
        } catch (BriefUnavailable) {
            // Unknown under this chart (404), or the agent could not answer:
            // either way nothing is known against it, so it is left alone.
            return null;
        }
    };
};

$owner = filter_var($session->get('authUserID'), FILTER_VALIDATE_INT);
$result = $documents->reconcile($chart->pid, $agent, $trace, $owner === false ? 0 : $owner);

foreach ($result->movedDocumentIds as $documentId) {
    // The co-pilot's own access record (AUDIT.md F-52): identifiers only.
    EventAuditLogger::getInstance()->newEvent(
        'patient-record',
        $session->get('authUser'),
        $session->get('authProvider'),
        1,
        'Clinical Co-Pilot document quarantined on card load: identity mismatch (documents.id ' . $documentId
            . ', correlation_id ' . $trace . ')',
        $chart->pid,
    );
}

$done($result->toPayload());
