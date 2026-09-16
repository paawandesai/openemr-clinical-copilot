<?php

/**
 * Calls the Clinical Co-Pilot agent's POST /pre-visit-brief with the
 * clinician's own token (docs/ARCHITECTURE.md §2).
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
     * Retrieval, one LLM call and verification: about 4 s in practice. Sized
     * so that, with the token broker's requests, the whole brief request
     * finishes inside PHP's 60 s max_execution_time.
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
        try {
            $response = $this->http->request('POST', $this->agentUrl . '/pre-visit-brief', [
                'auth_bearer' => $token->value,
                'json' => ['patient_id' => $patient->value],
                'max_redirects' => 0,
                'max_duration' => self::REQUEST_SECONDS,
            ]);
            $status = $response->getStatusCode();
            $body = $response->toArray(false);
        } catch (HttpClientException $e) {
            $this->logger->error('Clinical Co-Pilot agent request failed', ['exception' => $e]);
            throw new BriefUnavailable(BriefFailure::AgentUnavailable, null, $e);
        }

        $correlationId = is_string($body['correlation_id'] ?? null) ? $body['correlation_id'] : null;
        if ($status === 200) {
            try {
                return PreVisitBrief::fromAgentResponse($body);
            } catch (\UnexpectedValueException $e) {
                $this->logger->error('Clinical Co-Pilot agent response did not match its contract', [
                    'correlation_id' => $correlationId,
                    'exception' => $e,
                ]);
                throw new BriefUnavailable(BriefFailure::BriefGenerationFailed, $correlationId, $e);
            }
        }

        // ErrorResponse.error from agent/app/models/api.py.
        $error = is_string($body['error'] ?? null) ? $body['error'] : null;
        $failure = match (true) {
            $status === 401 => BriefFailure::AgentRejectedToken,
            $status === 404 => BriefFailure::PatientNotFound,
            $status === 502 && $error === 'fhir_unavailable' => BriefFailure::AgentUnavailable,
            $status >= 500 && $error !== null => BriefFailure::BriefGenerationFailed,
            default => BriefFailure::AgentUnavailable,
        };
        $this->logger->error('Clinical Co-Pilot agent did not return a brief', [
            'http_status' => $status,
            'agent_error' => $error,
            'correlation_id' => $correlationId,
        ]);
        throw new BriefUnavailable($failure, $correlationId);
    }
}
