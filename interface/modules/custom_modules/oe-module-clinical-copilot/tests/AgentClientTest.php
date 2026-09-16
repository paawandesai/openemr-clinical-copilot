<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests;

use OpenEMR\Modules\ClinicalCopilot\AccessToken;
use OpenEMR\Modules\ClinicalCopilot\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\PatientUuid;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;

final class AgentClientTest extends TestCase
{
    private const PATIENT = 'a2c0e096-23d5-4cf9-ad5b-4895597363a7';

    public function testSendsTheClinicianTokenAndPatientAndReturnsTheBrief(): void
    {
        $seen = null;
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$seen): MockResponse {
            $seen = new HttpExchange($method, $url, $options);
            return new MockResponse(json_encode([
                'brief' => "Pre-Visit Brief\n\nAllergies: not recorded",
                'patient_id' => self::PATIENT,
                'session_id' => 'session-1234',
                'patient_session_key' => 'user:' . self::PATIENT . ':session-1234',
                'correlation_id' => 'corr-1',
                'verification' => ['claims' => 2, 'verified' => 1, 'unverified' => 1],
                'claims' => [],
                'sections' => [],
                'timing' => [],
            ], JSON_THROW_ON_ERROR), ['http_code' => 200]);
        });

        $brief = (new AgentClient($http, new NullLogger(), 'https://agent.example:8443'))
            ->preVisitBrief(new AccessToken('jwt.token.here'), new PatientUuid(self::PATIENT));

        $this->assertInstanceOf(HttpExchange::class, $seen);
        $this->assertSame('POST', $seen->method);
        $this->assertSame('https://agent.example:8443/pre-visit-brief', $seen->url);
        $this->assertContains('Authorization: Bearer jwt.token.here', $seen->headerLines());
        $this->assertSame('{"patient_id":"' . self::PATIENT . '"}', $seen->body());

        $this->assertSame([
            'status' => 'ok',
            'brief' => "Pre-Visit Brief\n\nAllergies: not recorded",
            'verification' => ['claims' => 2, 'verified' => 1, 'unverified' => 1],
            'correlation_id' => 'corr-1',
        ], $brief->toCardPayload());
    }

    #[DataProvider('agentFailures')]
    public function testAgentFailuresMapToCardFailures(MockResponse $response, BriefFailure $expected, ?string $correlationId): void
    {
        $client = new AgentClient(new MockHttpClient($response), new NullLogger(), 'https://agent.example:8443');
        try {
            $client->preVisitBrief(new AccessToken('t'), new PatientUuid(self::PATIENT));
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame($expected, $e->failure);
            $this->assertSame($correlationId, $e->correlationId);
        }
    }

    /**
     * @return array<string, array{MockResponse, BriefFailure, ?string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function agentFailures(): array
    {
        $error = fn (int $status, string $code): MockResponse => new MockResponse(
            json_encode(['error' => $code, 'message' => 'm', 'correlation_id' => 'corr-' . $status], JSON_THROW_ON_ERROR),
            ['http_code' => $status],
        );
        return [
            'token OpenEMR rejects' => [$error(401, 'fhir_token_rejected'), BriefFailure::AgentRejectedToken, 'corr-401'],
            'unknown patient' => [$error(404, 'patient_not_found'), BriefFailure::PatientNotFound, 'corr-404'],
            'FHIR unreachable from the agent' => [$error(502, 'fhir_unavailable'), BriefFailure::AgentUnavailable, 'corr-502'],
            'LLM call failed' => [$error(502, 'brief_generation_failed'), BriefFailure::BriefGenerationFailed, 'corr-502'],
            'agent internal error' => [$error(500, 'internal_error'), BriefFailure::BriefGenerationFailed, 'corr-500'],
            'proxy error page, not JSON' => [new MockResponse('<html>502</html>', ['http_code' => 502]), BriefFailure::AgentUnavailable, null],
            'connection failure' => [new MockResponse('', ['error' => 'TLS handshake failed']), BriefFailure::AgentUnavailable, null],
            '200 that breaks the contract' => [
                new MockResponse('{"brief":"x","correlation_id":"c","verification":{"claims":3,"verified":1,"unverified":1}}', ['http_code' => 200]),
                BriefFailure::BriefGenerationFailed,
                'c',
            ],
        ];
    }
}
