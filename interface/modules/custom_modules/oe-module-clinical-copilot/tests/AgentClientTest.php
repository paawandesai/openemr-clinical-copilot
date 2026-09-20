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
use OpenEMR\Modules\ClinicalCopilot\ConversationNonce;
use OpenEMR\Modules\ClinicalCopilot\ConversationSessionId;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\FollowUpQuestion;
use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
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
            'patient' => ['name' => 'Phil Belford', 'date_of_birth' => '1972-02-09'],
            'brief' => "Pre-Visit Brief\n\nAllergies: not recorded",
            'verification' => ['claims' => 2, 'verified' => 1, 'unverified' => 1],
            'correlation_id' => 'corr-1',
        ], $brief->toCardPayload(self::openPatient()));
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
            'brief for a patient other than the one asked about' => [
                new MockResponse(
                    '{"brief":"x","patient_id":"a2c37430-0a29-4c57-8195-2c924c2b654a","correlation_id":"c","verification":{"claims":0,"verified":0,"unverified":0}}',
                    ['http_code' => 200],
                ),
                BriefFailure::BriefGenerationFailed,
                'c',
            ],
            '200 that breaks the contract' => [
                new MockResponse('{"brief":"x","correlation_id":"c","verification":{"claims":3,"verified":1,"unverified":1}}', ['http_code' => 200]),
                BriefFailure::BriefGenerationFailed,
                'c',
            ],
        ];
    }

    public function testSendsOneConversationTurnAndReturnsOnlyWhatTheCardShows(): void
    {
        $seen = null;
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$seen): MockResponse {
            $seen = new HttpExchange($method, $url, $options);
            return new MockResponse(json_encode(self::turnResponse(), JSON_THROW_ON_ERROR), ['http_code' => 200]);
        });
        $session = self::session();

        $turn = (new AgentClient($http, new NullLogger(), 'https://agent.example:8443'))
            ->conversationTurn(new AccessToken('jwt.token.here'), new PatientUuid(self::PATIENT), $session, new FollowUpQuestion('Which medications are active?'));

        $this->assertInstanceOf(HttpExchange::class, $seen);
        $this->assertSame('https://agent.example:8443/pre-visit-brief/conversation', $seen->url);
        $this->assertContains('Authorization: Bearer jwt.token.here', $seen->headerLines());
        $this->assertSame(
            ['patient_id' => self::PATIENT, 'session_id' => $session->value, 'question' => 'Which medications are active?'],
            json_decode($seen->body(), true, flags: JSON_THROW_ON_ERROR),
        );

        $payload = $turn->toCardPayload(self::openPatient());
        // F-62: every answer names the open patient, from the server's own record.
        $this->assertSame(['name' => 'Phil Belford', 'date_of_birth' => '1972-02-09'], $payload['patient']);
        $this->assertSame('partially_answered', $payload['turn_status']);
        $this->assertSame(['claims' => 2, 'verified' => 1, 'unverified' => 1], $payload['verification']);
        $this->assertSame('reset_patient_changed', $payload['conversation']['transition']);
        $this->assertSame('UNVERIFIED: Medication "Norvasc", status: stopped (value does not match the record)', $payload['claims'][1]['text']);
        $this->assertSame(['resource_type' => 'Observation', 'rule' => 'question_rule', 'retrieved' => 82, 'considered' => 5], $payload['retrieval'][1]);
        $this->assertSame(4.04, $payload['total_seconds']);
        // Identifiers the card has no use for stay on the server.
        $encoded = json_encode($payload, JSON_THROW_ON_ERROR);
        $this->assertStringNotContainsString($session->value, $encoded);
        $this->assertStringNotContainsString('user-uuid', $encoded);
        $this->assertStringNotContainsString(self::PATIENT, $encoded);
    }

    public function testARefusalIsATurnNotAFailure(): void
    {
        $body = self::turnResponse();
        $body['turn_status'] = 'refused_unverifiable';
        $body['claims'] = [];
        $body['retrieval'] = [];
        $body['verification'] = ['claims' => 0, 'verified' => 0, 'unverified' => 0];
        $client = new AgentClient(new MockHttpClient(new MockResponse(json_encode($body, JSON_THROW_ON_ERROR))), new NullLogger(), 'https://agent.example:8443');

        $turn = $client->conversationTurn(new AccessToken('t'), new PatientUuid(self::PATIENT), self::session(), new FollowUpQuestion('Is her kidney function worsening?'));

        $this->assertSame('refused_unverifiable', $turn->turnStatus);
        $this->assertSame([], $turn->claims);
        $this->assertSame('Phil Belford', $turn->toCardPayload(self::openPatient())['patient']['name']);
    }

    #[DataProvider('conversationFailures')]
    public function testConversationFailuresMapToCardFailures(MockResponse $response, BriefFailure $expected, ?string $correlationId): void
    {
        $client = new AgentClient(new MockHttpClient($response), new NullLogger(), 'https://agent.example:8443');
        try {
            $client->conversationTurn(new AccessToken('t'), new PatientUuid(self::PATIENT), self::session(), new FollowUpQuestion('q'));
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
    public static function conversationFailures(): array
    {
        $error = fn (int $status, string $code): MockResponse => new MockResponse(
            json_encode(['error' => $code, 'message' => 'm', 'correlation_id' => 'corr-' . $status], JSON_THROW_ON_ERROR),
            ['http_code' => $status],
        );
        $inconsistent = self::turnResponse();
        $inconsistent['verification'] = ['claims' => 2, 'verified' => 2, 'unverified' => 0];
        $anotherPatient = self::turnResponse();
        $anotherPatient['patient_id'] = 'a2c37430-0a29-4c57-8195-2c924c2b654a';
        $noPatient = self::turnResponse();
        unset($noPatient['patient_id']);
        $noClaimText = self::turnResponse();
        $noClaimText['claims'] = [['resource_type' => 'Condition', 'source_id' => 's', 'field' => 'display', 'verified' => true]];
        return [
            'token lacks a scope the question needs' => [$error(401, 'fhir_token_rejected'), BriefFailure::AgentRejectedToken, 'corr-401'],
            'unknown patient' => [$error(404, 'patient_not_found'), BriefFailure::PatientNotFound, 'corr-404'],
            'question the agent rejects' => [$error(422, 'invalid_request'), BriefFailure::InvalidQuestion, 'corr-422'],
            'FHIR unreachable from the agent' => [$error(502, 'fhir_unavailable'), BriefFailure::AgentUnavailable, 'corr-502'],
            'LLM call failed' => [$error(502, 'follow_up_generation_failed'), BriefFailure::AnswerGenerationFailed, 'corr-502'],
            'agent internal error' => [$error(500, 'internal_error'), BriefFailure::AnswerGenerationFailed, 'corr-500'],
            'an agent without the conversation endpoint' => [new MockResponse('{"detail":"Not Found"}', ['http_code' => 404]), BriefFailure::AgentUnavailable, null],
            'connection failure' => [new MockResponse('', ['error' => 'TLS handshake failed']), BriefFailure::AgentUnavailable, null],
            'answer for a patient other than the one asked about' => [new MockResponse(json_encode($anotherPatient, JSON_THROW_ON_ERROR)), BriefFailure::AnswerGenerationFailed, 'corr-turn'],
            'answer that does not say whom it is for' => [new MockResponse(json_encode($noPatient, JSON_THROW_ON_ERROR)), BriefFailure::AnswerGenerationFailed, 'corr-turn'],
            'summary that contradicts the claims' => [new MockResponse(json_encode($inconsistent, JSON_THROW_ON_ERROR)), BriefFailure::AnswerGenerationFailed, 'corr-turn'],
            'claim without its text' => [new MockResponse(json_encode($noClaimText, JSON_THROW_ON_ERROR)), BriefFailure::AnswerGenerationFailed, 'corr-turn'],
        ];
    }

    public function testEndsAConversation(): void
    {
        $seen = null;
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$seen): MockResponse {
            $seen = new HttpExchange($method, $url, $options);
            return new MockResponse('{"ended":true,"session_id":"x","correlation_id":"c"}', ['http_code' => 200]);
        });
        $session = self::session();

        (new AgentClient($http, new NullLogger(), 'https://agent.example:8443'))
            ->endConversation(new AccessToken('t'), new PatientUuid(self::PATIENT), $session);

        $this->assertInstanceOf(HttpExchange::class, $seen);
        $this->assertSame('https://agent.example:8443/pre-visit-brief/conversation/end', $seen->url);
        $this->assertSame(['patient_id' => self::PATIENT, 'session_id' => $session->value], json_decode($seen->body(), true, flags: JSON_THROW_ON_ERROR));
    }

    private static function openPatient(): OpenPatient
    {
        return OpenPatient::fromPatientData([
            'uuid' => hex2bin(str_replace('-', '', self::PATIENT)),
            'fname' => 'Phil',
            'mname' => '',
            'lname' => 'Belford',
            'DOB' => '1972-02-09',
        ]);
    }

    private static function session(): ConversationSessionId
    {
        return ConversationSessionId::derive(
            new CoreSessionId('abcdefghijklmnopqrstuvwxyz012345'),
            new PatientUuid(self::PATIENT),
            new ConversationNonce('0123456789abcdef0123456789abcdef'),
        );
    }

    /**
     * @return array<string, mixed> A ConversationTurnResponse (agent/app/models/api.py).
     */
    private static function turnResponse(): array
    {
        $session = self::session()->value;
        return [
            'answer' => "Follow-up answer (turn 1)\n\nPartly answered from the records retrieved for this turn:\n  - Medication \"Lisinopril\", status: active\n"
                . "  - UNVERIFIED: Medication \"Norvasc\", status: stopped (value does not match the record)\n\n"
                . "1 item(s) above could not be verified against the chart and are marked UNVERIFIED.\n\nAnswered only from what was retrieved for this turn.",
            'turn_status' => 'partially_answered',
            'refusals' => [],
            'gaps' => [],
            'question' => 'Which medications are active?',
            'patient_id' => self::PATIENT,
            'session_id' => $session,
            'patient_session_key' => 'user-uuid:' . self::PATIENT . ':' . $session,
            'correlation_id' => 'corr-turn',
            'conversation' => [
                'turn' => 1,
                'transition' => 'reset_patient_changed',
                'context_turns' => 0,
                'context_claims' => 0,
                'state_saved' => true,
                'expires_in_seconds' => 1800,
            ],
            'retrieval' => [
                ['resource_type' => 'MedicationRequest', 'rule' => 'base_set', 'retrieved' => 2, 'considered' => 2],
                ['resource_type' => 'Observation', 'rule' => 'question_rule', 'retrieved' => 82, 'considered' => 5],
            ],
            'verification' => ['claims' => 2, 'verified' => 1, 'unverified' => 1],
            'claims' => [
                [
                    'resource_type' => 'MedicationRequest', 'source_id' => 'med-1', 'field' => 'status', 'value' => 'active',
                    'verified' => true, 'unverified_reason' => null, 'text' => 'Medication "Lisinopril", status: active',
                ],
                [
                    'resource_type' => 'MedicationRequest', 'source_id' => 'med-2', 'field' => 'status', 'value' => 'stopped',
                    'verified' => false, 'unverified_reason' => 'value does not match the record',
                    'text' => 'UNVERIFIED: Medication "Norvasc", status: stopped (value does not match the record)',
                ],
            ],
            'sections' => [],
            'timing' => ['retrieve_seconds' => 1.0, 'draft_seconds' => 2.5, 'verify_seconds' => 0.04, 'render_seconds' => 0.5, 'total_seconds' => 4.04],
        ];
    }
}
