<?php

/**
 * Calls the Clinical Co-Pilot agent with the clinician's own token
 * (docs/ARCHITECTURE.md §2): POST /pre-visit-brief, and the follow-up
 * conversation's POST /pre-visit-brief/conversation and .../conversation/end.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

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
     * @param HttpClientInterface $http Configured with the agent's CA; certificate verification on.
     * @param string $agentUrl Base URL, no trailing slash.
     */
    public function __construct(
        private HttpClientInterface $http,
        private LoggerInterface $logger,
        private string $agentUrl,
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
     * @param array<string, string> $json
     * @return array{int, array<mixed>}
     * @throws BriefUnavailable
     */
    private function post(string $path, #[\SensitiveParameter] AccessToken $token, #[\SensitiveParameter] array $json): array
    {
        try {
            $response = $this->http->request('POST', $this->agentUrl . $path, [
                'auth_bearer' => $token->value,
                'json' => $json,
                'max_redirects' => 0,
                'max_duration' => self::REQUEST_SECONDS,
            ]);
            return [$response->getStatusCode(), $response->toArray(false)];
        } catch (HttpClientException $e) {
            $this->logger->error('Clinical Co-Pilot agent request failed', ['path' => $path, 'exception' => $e]);
            throw new BriefUnavailable(BriefFailure::AgentUnavailable, null, $e);
        }
    }

    /**
     * @param array<mixed> $body ErrorResponse from agent/app/models/api.py, when the agent itself answered.
     * @param BriefFailure $generationFailed What a failure inside the agent's own pipeline means for this call.
     */
    private function failed(int $status, array $body, BriefFailure $generationFailed): BriefUnavailable
    {
        $correlationId = self::correlationId($body);
        $error = is_string($body['error'] ?? null) ? $body['error'] : null;
        $failure = match (true) {
            $status === 401 => BriefFailure::AgentRejectedToken,
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
