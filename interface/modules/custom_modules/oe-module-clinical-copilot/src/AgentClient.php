<?php

/**
 * Calls the Clinical Co-Pilot agent with the clinician's own token
 * (docs/ARCHITECTURE.md §2): POST /pre-visit-brief, and the follow-up
 * conversation's POST /pre-visit-brief/conversation and .../conversation/end;
 * and for the Week 2 card, POST /documents, GET /documents/{id}/extraction
 * and POST /w2/ask (docs/w2/ui-integration.md §2.2-2.4).
 *
 * Given a CorrelationId, every request carries it as X-Correlation-ID, so the
 * agent logs the whole chart open — upload, extraction, question — under one
 * id. Without one the agent mints its own per request, as before.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Modules\ClinicalCopilot\W2\AgentDocumentId;
use OpenEMR\Modules\ClinicalCopilot\W2\AskAnswer;
use OpenEMR\Modules\ClinicalCopilot\W2\DocumentKind;
use OpenEMR\Modules\ClinicalCopilot\W2\ExtractionState;
use Psr\Log\LoggerInterface;
use Symfony\Contracts\HttpClient\Exception\ExceptionInterface as HttpClientException;
use Symfony\Contracts\HttpClient\HttpClientInterface;

final readonly class AgentClient
{
    /**
     * Retrieval, one LLM call and verification: about 4 s for a brief, and up
     * to about 15 s for a conversation turn that cites many records. Sized so
     * that, with the token broker's requests, the whole request finishes
     * inside PHP's 60 s max_execution_time.
     */
    private const REQUEST_SECONDS = 40.0;

    /**
     * An upload returns once the file is hashed and the job started (D7);
     * the time is the transfer of up to 25 MB and one Patient read. A status
     * poll is one Patient read and a dictionary lookup.
     */
    private const UPLOAD_SECONDS = 30.0;
    private const STATUS_SECONDS = 10.0;

    /**
     * @param HttpClientInterface $http Configured with the agent's CA; certificate verification on.
     * @param string $agentUrl Base URL, no trailing slash.
     */
    public function __construct(
        private HttpClientInterface $http,
        private LoggerInterface $logger,
        private string $agentUrl,
        private ?CorrelationId $correlationId = null,
    ) {
    }

    /**
     * @throws BriefUnavailable
     */
    public function preVisitBrief(#[\SensitiveParameter] AccessToken $token, PatientUuid $patient): PreVisitBrief
    {
        [$status, $body] = $this->post('/pre-visit-brief', $token, ['patient_id' => $patient->value]);
        if ($status !== 200) {
            throw $this->failed($status, $body, BriefFailure::BriefGenerationFailed);
        }
        try {
            self::assertAnsweredFor($patient, $body);
            return PreVisitBrief::fromAgentResponse($body);
        } catch (\UnexpectedValueException $e) {
            throw $this->brokeContract($body, BriefFailure::BriefGenerationFailed, $e);
        }
    }

    /**
     * One turn of the follow-up conversation (agent/app/api/conversation.py).
     * A refusal is a 200 and comes back as a ConversationTurn, not a failure.
     *
     * @throws BriefUnavailable
     */
    public function conversationTurn(
        #[\SensitiveParameter] AccessToken $token,
        PatientUuid $patient,
        ConversationSessionId $session,
        #[\SensitiveParameter] FollowUpQuestion $question,
    ): ConversationTurn {
        [$status, $body] = $this->post('/pre-visit-brief/conversation', $token, [
            'patient_id' => $patient->value,
            'session_id' => $session->value,
            'question' => $question->value,
        ]);
        if ($status !== 200) {
            throw $this->failed($status, $body, BriefFailure::AnswerGenerationFailed);
        }
        try {
            self::assertAnsweredFor($patient, $body);
            return ConversationTurn::fromAgentResponse($body);
        } catch (\UnexpectedValueException $e) {
            throw $this->brokeContract($body, BriefFailure::AnswerGenerationFailed, $e);
        }
    }

    /**
     * Discards the conversation's verified facts on the agent now, rather
     * than at their idle expiry.
     *
     * @throws BriefUnavailable
     */
    public function endConversation(#[\SensitiveParameter] AccessToken $token, PatientUuid $patient, ConversationSessionId $session): void
    {
        [$status, $body] = $this->post('/pre-visit-brief/conversation/end', $token, [
            'patient_id' => $patient->value,
            'session_id' => $session->value,
        ]);
        if ($status !== 200) {
            throw $this->failed($status, $body, BriefFailure::AgentUnavailable);
        }
    }

    /**
     * Hands a PDF to the agent, which starts extracting it at once (D7), for
     * the open chart's patient. The body is the PDF itself, not multipart
     * (ui-integration.md §2.2). `patient_id` is required since D40 item 8:
     * the agent reads that chart for its identity and discrepancy checks,
     * and refuses (409) bytes it already holds for a different chart.
     *
     * @throws BriefUnavailable
     */
    public function uploadDocument(
        #[\SensitiveParameter] AccessToken $token,
        PatientUuid $patient,
        DocumentKind $kind,
        #[\SensitiveParameter] string $pdf,
    ): ExtractionState {
        [$status, $body] = $this->request('POST', '/documents', $token, [
            'query' => ['kind' => $kind->value, 'patient_id' => $patient->value],
            'headers' => ['Content-Type' => 'application/pdf'],
            'body' => $pdf,
        ], self::UPLOAD_SECONDS);
        if ($status !== 200) {
            throw $this->failed($status, $body, BriefFailure::AgentUnavailable, [
                400 => BriefFailure::UploadRejected,
                409 => BriefFailure::DocumentAttachedElsewhere,
                413 => BriefFailure::UploadRejected,
                415 => BriefFailure::UploadRejected,
                422 => BriefFailure::UploadRejected,
            ]);
        }
        try {
            return ExtractionState::fromUpload($body, AgentDocumentId::ofBytes($pdf));
        } catch (\UnexpectedValueException $e) {
            throw $this->brokeContract($body, BriefFailure::AgentUnavailable, $e);
        }
    }

    /**
     * How far a document's extraction has got. Never waits (D7).
     *
     * @throws BriefUnavailable
     */
    public function extractionState(#[\SensitiveParameter] AccessToken $token, PatientUuid $patient, AgentDocumentId $document): ExtractionState
    {
        [$status, $body] = $this->request(
            'GET',
            '/documents/' . $document->value . '/extraction',
            $token,
            ['query' => ['patient_id' => $patient->value]],
            self::STATUS_SECONDS,
        );
        if ($status !== 200) {
            $error = is_string($body['error'] ?? null) ? $body['error'] : null;
            // A 404 that is not about the patient is "no extraction recorded
            // for that document on this chart" — unknown and another
            // patient's are one answer (ui-integration.md §2.3).
            $overrides = $error === 'patient_not_found' ? [] : [404 => BriefFailure::ExtractionNotFound];
            throw $this->failed($status, $body, BriefFailure::AgentUnavailable, $overrides);
        }
        try {
            return ExtractionState::fromStatus($body, $document);
        } catch (\UnexpectedValueException $e) {
            throw $this->brokeContract($body, BriefFailure::AgentUnavailable, $e);
        }
    }

    /**
     * One turn of the Week 2 ask path (agent/app/api/ask.py). A refusal, a
     * document still processing and an answer are all a 200 and come back
     * as an AskAnswer, not a failure.
     *
     * @param list<AgentDocumentId> $documents
     * @throws BriefUnavailable
     */
    public function ask(
        #[\SensitiveParameter] AccessToken $token,
        PatientUuid $patient,
        ConversationSessionId $session,
        #[\SensitiveParameter] FollowUpQuestion $question,
        array $documents,
    ): AskAnswer {
        [$status, $body] = $this->request('POST', '/w2/ask', $token, [
            'json' => [
                'patient_id' => $patient->value,
                'session_id' => $session->value,
                'question' => $question->value,
                'document_ids' => array_map(static fn (AgentDocumentId $id): string => $id->value, $documents),
            ],
        ], self::REQUEST_SECONDS);
        if ($status !== 200) {
            throw $this->failed($status, $body, BriefFailure::AnswerGenerationFailed);
        }
        try {
            return AskAnswer::fromAgentResponse($body, $patient, $session);
        } catch (\UnexpectedValueException $e) {
            throw $this->brokeContract($body, BriefFailure::AnswerGenerationFailed, $e);
        }
    }

    /**
     * @param array<string, string> $json
     * @return array{int, array<mixed>}
     * @throws BriefUnavailable
     */
    private function post(string $path, #[\SensitiveParameter] AccessToken $token, #[\SensitiveParameter] array $json): array
    {
        return $this->request('POST', $path, $token, ['json' => $json], self::REQUEST_SECONDS);
    }

    /**
     * @param array<string, mixed> $options Symfony HttpClient options: json, body, query, headers.
     * @return array{int, array<mixed>}
     * @throws BriefUnavailable
     */
    private function request(
        string $method,
        string $path,
        #[\SensitiveParameter] AccessToken $token,
        #[\SensitiveParameter] array $options,
        float $seconds,
    ): array {
        $headers = is_array($options['headers'] ?? null) ? $options['headers'] : [];
        if ($this->correlationId !== null) {
            $headers[CorrelationId::HEADER] = $this->correlationId->value;
        }
        try {
            $response = $this->http->request($method, $this->agentUrl . $path, [
                'auth_bearer' => $token->value,
                'headers' => $headers,
                'max_redirects' => 0,
                'max_duration' => $seconds,
            ] + $options);
            return [$response->getStatusCode(), $response->toArray(false)];
        } catch (HttpClientException $e) {
            // The path of a status poll names a document id, which is a
            // digest, not chart content; the query (patient id) is not logged.
            $this->logger->error('Clinical Co-Pilot agent request failed', ['path' => $path, 'exception' => $e]);
            throw new BriefUnavailable(BriefFailure::AgentUnavailable, $this->correlationId?->value, $e);
        }
    }

    /**
     * @param array<mixed> $body ErrorResponse from agent/app/models/api.py, when the agent itself answered.
     * @param BriefFailure $generationFailed What a failure inside the agent's own pipeline means for this call.
     * @param array<int, BriefFailure> $byStatus What an HTTP status means for this route, checked first.
     */
    private function failed(int $status, array $body, BriefFailure $generationFailed, array $byStatus = []): BriefUnavailable
    {
        $correlationId = self::correlationId($body);
        $error = is_string($body['error'] ?? null) ? $body['error'] : null;
        $failure = $byStatus[$status] ?? match (true) {
            $status === 401 => BriefFailure::AgentRejectedToken,
            // The token lacks a scope the route reads with (upload_guard.py).
            $status === 403 && $error === 'insufficient_scope' => BriefFailure::AgentRejectedToken,
            $status === 429 && $error === 'rate_limited' => BriefFailure::RateLimited,
            $status === 503 && $error === 'extraction_capacity' => BriefFailure::ExtractionBusy,
            // A bare 404 is a route the deployed agent does not have, not a missing patient.
            $status === 404 && $error === 'patient_not_found' => BriefFailure::PatientNotFound,
            $status === 422 && $error === 'invalid_request' => BriefFailure::InvalidQuestion,
            $status === 502 && $error === 'fhir_unavailable' => BriefFailure::AgentUnavailable,
            $status >= 500 && $error !== null => $generationFailed,
            default => BriefFailure::AgentUnavailable,
        };
        $this->logger->error('Clinical Co-Pilot agent answered with an error', [
            'http_status' => $status,
            'agent_error' => $error,
            'correlation_id' => $correlationId,
        ]);
        return new BriefUnavailable($failure, $correlationId);
    }

    /**
     * @param array<mixed> $body
     */
    private function brokeContract(array $body, BriefFailure $failure, \UnexpectedValueException $e): BriefUnavailable
    {
        $correlationId = self::correlationId($body);
        $this->logger->error('Clinical Co-Pilot agent response did not match its contract', [
            'correlation_id' => $correlationId,
            'exception' => $e,
        ]);
        return new BriefUnavailable($failure, $correlationId, $e);
    }

    /**
     * The card names the open patient above what the agent returns (F-62), so
     * the agent must say it answered for that patient.
     *
     * @param array<mixed> $body
     * @throws \UnexpectedValueException
     */
    private static function assertAnsweredFor(PatientUuid $patient, array $body): void
    {
        if (($body['patient_id'] ?? null) !== $patient->value) {
            throw new \UnexpectedValueException('Agent response is not for the patient it was asked about');
        }
    }

    /**
     * @param array<mixed> $body
     */
    private static function correlationId(array $body): ?string
    {
        return is_string($body['correlation_id'] ?? null) ? $body['correlation_id'] : null;
    }
}
