<?php

/**
 * The Week 2 agent calls: upload, extraction status, ask. What is sent (raw
 * PDF body, patient_id on every hop, X-Correlation-ID), what the card gets
 * back (`status` renamed, the session key dropped), and how each agent
 * failure maps onto a card failure — including D40 item 8's 409.
 *
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
use OpenEMR\Modules\ClinicalCopilot\CorrelationId;
use OpenEMR\Modules\ClinicalCopilot\FollowUpQuestion;
use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
use OpenEMR\Modules\ClinicalCopilot\PatientUuid;
use OpenEMR\Modules\ClinicalCopilot\W2\AgentDocumentId;
use OpenEMR\Modules\ClinicalCopilot\W2\DocumentKind;
use OpenEMR\Modules\ClinicalCopilot\W2\QuarantineOutcome;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;

final class AgentClientW2Test extends TestCase
{
    private const PATIENT = 'a2c0e096-23d5-4cf9-ad5b-4895597363a7';
    private const TRACE = '3f2b9c1e-7d4a-4e8b-9f0c-1a2b3c4d5e6f';
    private const PDF = "%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n";

    public function testUploadSendsTheRawPdfForTheOpenPatientWithTheCorrelationId(): void
    {
        $seen = null;
        $digest = hash('sha3-512', self::PDF);
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$seen, $digest): MockResponse {
            $seen = new HttpExchange($method, $url, $options);
            return self::json(200, [
                'document_id' => $digest,
                'kind' => 'lab_pdf',
                'status' => 'pending',
                'byte_length' => strlen(self::PDF),
                'correlation_id' => self::TRACE,
            ]);
        });

        $state = self::client($http)->uploadDocument(self::token(), self::patient(), DocumentKind::LabPdf, self::PDF);

        $this->assertInstanceOf(HttpExchange::class, $seen);
        $this->assertSame('POST', $seen->method);
        $this->assertStringStartsWith('https://agent.example:8443/documents?', $seen->url);
        // D40 item 8: the open chart's patient on the upload hop.
        $this->assertSame(['kind' => 'lab_pdf', 'patient_id' => self::PATIENT], $seen->query());
        $this->assertSame(self::PDF, $seen->body());
        $this->assertContains('Content-Type: application/pdf', $seen->headerLines());
        $this->assertContains('Authorization: Bearer jwt.token.here', $seen->headerLines());
        $this->assertContains('X-Correlation-ID: ' . self::TRACE, $seen->headerLines());

        // `status` is the envelope; the extraction state is renamed.
        $this->assertSame([
            'status' => 'ok',
            'document_id' => $digest,
            'kind' => 'lab_pdf',
            'extraction_status' => 'pending',
            'byte_length' => strlen(self::PDF),
            'correlation_id' => self::TRACE,
            // D60: no verdict before the extraction completes, so nothing to quarantine.
            'identity_check' => null,
            'quarantine' => null,
            // Work order 34: no write-back has ended either.
            'write_back' => null,
        ], $state->toUploadPayload());
    }

    public function testAnUploadAnsweredForOtherBytesIsAContractBreak(): void
    {
        $http = new MockHttpClient(self::json(200, [
            'document_id' => str_repeat('a', 128),
            'kind' => 'lab_pdf',
            'status' => 'pending',
            'byte_length' => 1,
            'correlation_id' => self::TRACE,
        ]));
        $this->expectFailure(BriefFailure::AgentUnavailable, fn () => self::client($http)
            ->uploadDocument(self::token(), self::patient(), DocumentKind::LabPdf, self::PDF));
    }

    #[DataProvider('uploadFailures')]
    public function testUploadFailuresMapToCardFailures(int $status, string $error, BriefFailure $expected): void
    {
        $http = new MockHttpClient(self::json($status, ['error' => $error, 'message' => 'x', 'correlation_id' => self::TRACE]));
        $this->expectFailure($expected, fn () => self::client($http)
            ->uploadDocument(self::token(), self::patient(), DocumentKind::IntakeForm, self::PDF));
    }

    /**
     * @return array<string, array{int, string, BriefFailure}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function uploadFailures(): array
    {
        return [
            'same bytes on another chart (D40 item 8)' => [409, 'invalid_request', BriefFailure::DocumentAttachedElsewhere],
            'empty' => [400, 'invalid_request', BriefFailure::UploadRejected],
            'too large' => [413, 'invalid_request', BriefFailure::UploadRejected],
            'not a readable PDF' => [415, 'invalid_request', BriefFailure::UploadRejected],
            'bad kind or patient id' => [422, 'invalid_request', BriefFailure::UploadRejected],
            'token rejected' => [401, 'fhir_token_rejected', BriefFailure::AgentRejectedToken],
            'token lacks an extraction scope' => [403, 'insufficient_scope', BriefFailure::AgentRejectedToken],
            'unknown patient' => [404, 'patient_not_found', BriefFailure::PatientNotFound],
            'rate limited' => [429, 'rate_limited', BriefFailure::RateLimited],
            'no free extraction slot' => [503, 'extraction_capacity', BriefFailure::ExtractionBusy],
            'OpenEMR unreachable from the agent' => [502, 'fhir_unavailable', BriefFailure::AgentUnavailable],
        ];
    }

    public function testStatusAsksForThisDocumentOnThisChart(): void
    {
        $seen = null;
        $document = new AgentDocumentId(hash('sha3-512', self::PDF));
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$seen, $document): MockResponse {
            $seen = new HttpExchange($method, $url, $options);
            return self::json(200, [
                'document_id' => $document->value,
                'kind' => 'intake_form',
                'status' => 'complete',
                'extraction_id' => 'w2x-0123456789abcdef0123456789abcdef',
                'failure_reason' => null,
                'correlation_id' => self::TRACE,
            ]);
        });

        $state = self::client($http)->extractionState(self::token(), self::patient(), $document);

        $this->assertInstanceOf(HttpExchange::class, $seen);
        $this->assertSame('GET', $seen->method);
        $this->assertStringStartsWith('https://agent.example:8443/documents/' . $document->value . '/extraction?', $seen->url);
        $this->assertSame(['patient_id' => self::PATIENT], $seen->query());
        $this->assertContains('X-Correlation-ID: ' . self::TRACE, $seen->headerLines());
        $this->assertSame([
            'status' => 'ok',
            'document_id' => $document->value,
            'kind' => 'intake_form',
            'extraction_status' => 'complete',
            'extraction_id' => 'w2x-0123456789abcdef0123456789abcdef',
            'failure_reason' => null,
            'correlation_id' => self::TRACE,
            'identity_check' => null,
            'quarantine' => null,
            'write_back' => null,
        ], $state->toStatusPayload());
        $this->assertFalse($state->isIdentityMismatch());
    }

    public function testTheWriteBackSummaryReachesTheCardFieldByFieldAndNothingElseDoes(): void
    {
        $document = new AgentDocumentId(hash('sha3-512', self::PDF));
        $conflict = [
            'test_code' => '2345-7',
            'test_name' => 'Glucose',
            'collection_date' => '2026-08-30',
            'collected_at' => '2026-08-30T07:12:00',
            'page' => 1,
            'bbox' => ['page' => 1, 'x0' => 270.4, 'y0' => 256.3, 'x1' => 280, 'y1' => 268.1],
            'document_value' => '89',
            'document_unit' => 'mg/dL',
            'chart_value' => '92',
            'chart_unit' => 'mg/dL',
            'chart_procedure_result_id' => 14912,
            'surprise' => 'not for the browser',
        ];
        $http = new MockHttpClient(self::json(200, [
            'document_id' => $document->value,
            'kind' => 'lab_pdf',
            'status' => 'complete',
            'extraction_id' => 'w2x-0123456789abcdef0123456789abcdef',
            'failure_reason' => null,
            'correlation_id' => self::TRACE,
            'identity_check' => 'confirmed',
            'write_back' => [
                'decision' => 'written',
                'results_total' => 22,
                'skipped' => 1,
                'created' => 19,
                'duplicate_ignored' => 0,
                'already_in_chart' => 1,
                'conflict' => 1,
                'conflicts' => [$conflict],
                'extra' => 'dropped',
            ],
        ]));

        $payload = self::client($http)->extractionState(self::token(), self::patient(), $document)->toStatusPayload();

        $expected = $conflict;
        unset($expected['surprise']);
        $expected['bbox'] = ['page' => 1, 'x0' => 270.4, 'y0' => 256.3, 'x1' => 280.0, 'y1' => 268.1];
        $this->assertSame([
            'decision' => 'written',
            'results_total' => 22,
            'skipped' => 1,
            'created' => 19,
            'duplicate_ignored' => 0,
            'already_in_chart' => 1,
            'conflict' => 1,
            'conflicts' => [$expected],
        ], $payload['write_back']);
    }

    public function testAWriteBackThatEndedWithoutWritingCarriesNoConflicts(): void
    {
        $document = new AgentDocumentId(hash('sha3-512', self::PDF));
        $http = new MockHttpClient(self::json(200, [
            'document_id' => $document->value,
            'kind' => 'lab_pdf',
            'status' => 'failed',
            'extraction_id' => null,
            'failure_reason' => 'timed_out',
            'correlation_id' => self::TRACE,
            'identity_check' => null,
            'write_back' => ['decision' => 'extraction_not_complete', 'results_total' => 0, 'skipped' => 0, 'created' => 0, 'duplicate_ignored' => 0, 'already_in_chart' => 0, 'conflict' => 0, 'conflicts' => []],
        ]));

        $payload = self::client($http)->extractionState(self::token(), self::patient(), $document)->toStatusPayload();

        $this->assertSame('extraction_not_complete', $payload['write_back']['decision'] ?? null);
        $this->assertSame([], $payload['write_back']['conflicts'] ?? null);
    }

    public function testAMismatchIsReportedAndCarriesTheQuarantineOutcomeToTheCard(): void
    {
        $document = new AgentDocumentId(hash('sha3-512', self::PDF));
        $http = new MockHttpClient(self::json(200, [
            'document_id' => $document->value,
            'kind' => 'lab_pdf',
            'status' => 'complete',
            'extraction_id' => 'w2x-0123456789abcdef0123456789abcdef',
            'failure_reason' => null,
            'correlation_id' => self::TRACE,
            'identity_check' => 'mismatch',
        ]));

        $state = self::client($http)->extractionState(self::token(), self::patient(), $document);

        $this->assertTrue($state->isIdentityMismatch());
        $payload = $state->toStatusPayload(QuarantineOutcome::Quarantined);
        $this->assertSame('mismatch', $payload['identity_check']);
        $this->assertSame('quarantined', $payload['quarantine']);
    }

    public function testAnInterruptedExtractionIsAFailureReasonTheModuleAccepts(): void
    {
        $document = new AgentDocumentId(hash('sha3-512', self::PDF));
        $http = new MockHttpClient(self::json(200, [
            'document_id' => $document->value,
            'kind' => 'lab_pdf',
            'status' => 'failed',
            'extraction_id' => null,
            'failure_reason' => 'interrupted',
            'correlation_id' => self::TRACE,
            'identity_check' => null,
        ]));

        $state = self::client($http)->extractionState(self::token(), self::patient(), $document);

        $this->assertSame('interrupted', $state->toStatusPayload()['failure_reason']);
        $this->assertFalse($state->isIdentityMismatch());
    }

    public function testAnUnknownOrOtherChartsDocumentIsExtractionNotFound(): void
    {
        $http = new MockHttpClient(self::json(404, ['error' => 'invalid_request', 'message' => 'x', 'correlation_id' => self::TRACE]));
        $this->expectFailure(BriefFailure::ExtractionNotFound, fn () => self::client($http)
            ->extractionState(self::token(), self::patient(), new AgentDocumentId(str_repeat('b', 128))));
    }

    public function testAnUnknownPatientOnTheStatusHopIsPatientNotFound(): void
    {
        $http = new MockHttpClient(self::json(404, ['error' => 'patient_not_found', 'message' => 'x', 'correlation_id' => self::TRACE]));
        $this->expectFailure(BriefFailure::PatientNotFound, fn () => self::client($http)
            ->extractionState(self::token(), self::patient(), new AgentDocumentId(str_repeat('b', 128))));
    }

    /**
     * @param array<string, mixed> $body
     */
    #[DataProvider('impossibleStates')]
    public function testAStatusTheAgentMayNotReportIsAContractBreak(array $body): void
    {
        $document = str_repeat('c', 128);
        $http = new MockHttpClient(self::json(200, $body + ['document_id' => $document, 'kind' => 'lab_pdf', 'correlation_id' => self::TRACE]));
        $this->expectFailure(BriefFailure::AgentUnavailable, fn () => self::client($http)
            ->extractionState(self::token(), self::patient(), new AgentDocumentId($document)));
    }

    /**
     * @return array<string, array{array<string, mixed>}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function impossibleStates(): array
    {
        return [
            'an extraction id before complete' => [['status' => 'in_progress', 'extraction_id' => 'w2x-1', 'failure_reason' => null]],
            'a failure reason while pending' => [['status' => 'pending', 'extraction_id' => null, 'failure_reason' => 'timed_out']],
            'an unknown failure reason' => [['status' => 'failed', 'extraction_id' => null, 'failure_reason' => 'because']],
            'an unknown status' => [['status' => 'queued', 'extraction_id' => null, 'failure_reason' => null]],
            'an identity check before complete' => [['status' => 'in_progress', 'extraction_id' => null, 'failure_reason' => null, 'identity_check' => 'mismatch']],
            'an identity check on a failure' => [['status' => 'failed', 'extraction_id' => null, 'failure_reason' => 'timed_out', 'identity_check' => 'confirmed']],
            'an unknown identity check' => [['status' => 'complete', 'extraction_id' => 'w2x-0123456789abcdef0123456789abcdef', 'failure_reason' => null, 'identity_check' => 'probably']],
            'a write-back before the extraction ends' => [['status' => 'in_progress', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack()]],
            'a write-back decision outside the closed set' => [['status' => 'complete', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack(['decision' => 'maybe'])]],
            'a conflict count that is not its conflicts' => [['status' => 'complete', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack(['conflict' => 2])]],
            'a negative count' => [['status' => 'complete', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack(['created' => -1])]],
            'a conflict without its chart row' => [['status' => 'complete', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack(['conflict' => 1, 'conflicts' => [self::conflict(['chart_procedure_result_id' => 0])]])]],
            'a conflict without a chart value' => [['status' => 'complete', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack(['conflict' => 1, 'conflicts' => [self::conflict(['chart_value' => ''])]])]],
            'a conflict whose region is on another page' => [['status' => 'complete', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack(['conflict' => 1, 'conflicts' => [self::conflict(['page' => 2])]])]],
            'a conflict whose region is not numbers' => [['status' => 'complete', 'extraction_id' => null, 'failure_reason' => null, 'write_back' => self::writeBack(['conflict' => 1, 'conflicts' => [self::conflict(['bbox' => ['page' => 1, 'x0' => 'left', 'y0' => 1, 'x1' => 2, 'y1' => 3]])]])]],
        ];
    }

    /**
     * @param array<string, mixed> $overrides
     * @return array<string, mixed>
     *
     * @codeCoverageIgnore Builder for the data provider above.
     */
    private static function writeBack(array $overrides = []): array
    {
        return $overrides + [
            'decision' => 'written', 'results_total' => 1, 'skipped' => 0, 'created' => 1,
            'duplicate_ignored' => 0, 'already_in_chart' => 0, 'conflict' => 0, 'conflicts' => [],
        ];
    }

    /**
     * @param array<string, mixed> $overrides
     * @return array<string, mixed>
     *
     * @codeCoverageIgnore Builder for the data provider above.
     */
    private static function conflict(array $overrides = []): array
    {
        return $overrides + [
            'test_code' => '2345-7', 'test_name' => 'Glucose', 'collection_date' => '2026-08-30', 'collected_at' => null,
            'page' => 1, 'bbox' => ['page' => 1, 'x0' => 1, 'y0' => 2, 'x1' => 3, 'y1' => 4],
            'document_value' => '89', 'document_unit' => 'mg/dL', 'chart_value' => '92', 'chart_unit' => 'mg/dL',
            'chart_procedure_result_id' => 5,
        ];
    }

    public function testAskSendsTheQuestionAndDocumentsAndReturnsANamedAnswerWithoutTheSessionKey(): void
    {
        $seen = null;
        $session = self::session();
        $documents = [new AgentDocumentId(str_repeat('d', 128))];
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$seen, $session): MockResponse {
            $seen = new HttpExchange($method, $url, $options);
            return self::json(200, self::askBody('user-1:' . self::PATIENT . ':' . $session->value));
        });

        $answer = self::client($http)->ask(self::token(), self::patient(), $session, new FollowUpQuestion('Is the potassium flagged?'), $documents);

        $this->assertInstanceOf(HttpExchange::class, $seen);
        $this->assertSame('https://agent.example:8443/w2/ask', $seen->url);
        $this->assertContains('X-Correlation-ID: ' . self::TRACE, $seen->headerLines());
        $this->assertSame([
            'patient_id' => self::PATIENT,
            'session_id' => $session->value,
            'question' => 'Is the potassium flagged?',
            'document_ids' => [str_repeat('d', 128)],
        ], json_decode($seen->body(), true, flags: JSON_THROW_ON_ERROR));

        $payload = $answer->toCardPayload(self::openPatient());
        $this->assertSame('ok', $payload['status']);
        $this->assertSame('answered', $payload['answer_status']);
        $this->assertSame(['name' => 'Phil Belford', 'date_of_birth' => '1972-02-09'], $payload['patient']);
        $view = $payload['answer_view'];
        $this->assertIsArray($view);
        $this->assertSame('w2-ui/answer-view/1.0.0', $view['schema_version'] ?? null);
        $this->assertArrayNotHasKey('patient_session_key', $payload);
        $this->assertArrayNotHasKey('routing', $payload);
        $this->assertStringNotContainsString($session->value, json_encode($payload, JSON_THROW_ON_ERROR));
    }

    #[DataProvider('foreignSessionKeys')]
    public function testAnAnswerForAnotherPatientOrConversationIsRejected(string $key): void
    {
        $http = new MockHttpClient(self::json(200, self::askBody($key)));
        $this->expectFailure(BriefFailure::AnswerGenerationFailed, fn () => self::client($http)
            ->ask(self::token(), self::patient(), self::session(), new FollowUpQuestion('q'), []));
    }

    /**
     * @return array<string, array{string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function foreignSessionKeys(): array
    {
        return [
            'another patient' => ['user-1:00000000-0000-4000-8000-000000000000:' . self::session()->value],
            'another conversation' => ['user-1:' . self::PATIENT . ':someone-elses-session'],
            'not a session key' => [self::PATIENT],
        ];
    }

    public function testAnAnswerViewOfAnUnknownSchemaIsRejected(): void
    {
        $body = self::askBody('user-1:' . self::PATIENT . ':' . self::session()->value);
        $body['answer_view'] = ['schema_version' => 'w2-ui/answer-view/2.0.0'];
        $http = new MockHttpClient(self::json(200, $body));
        $this->expectFailure(BriefFailure::AnswerGenerationFailed, fn () => self::client($http)
            ->ask(self::token(), self::patient(), self::session(), new FollowUpQuestion('q'), []));
    }

    public function testAskHttpFailuresMapLikeAConversationTurn(): void
    {
        $http = new MockHttpClient(self::json(500, ['error' => 'internal_error', 'message' => 'x', 'correlation_id' => self::TRACE]));
        $this->expectFailure(BriefFailure::AnswerGenerationFailed, fn () => self::client($http)
            ->ask(self::token(), self::patient(), self::session(), new FollowUpQuestion('q'), []));
    }

    public function testWithoutACorrelationIdNoHeaderIsSent(): void
    {
        $seen = null;
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$seen): MockResponse {
            $seen = new HttpExchange($method, $url, $options);
            return self::json(200, ['status' => 'ended']);
        });
        (new AgentClient($http, new NullLogger(), 'https://agent.example:8443'))
            ->endConversation(self::token(), self::patient(), self::session());

        $this->assertInstanceOf(HttpExchange::class, $seen);
        foreach ($seen->headerLines() as $line) {
            $this->assertStringStartsNotWith('X-Correlation-ID', $line);
        }
    }

    public function testAnUnreachableAgentReportsTheModulesCorrelationId(): void
    {
        $http = new MockHttpClient(new MockResponse('', ['error' => 'connection refused']));
        try {
            self::client($http)->extractionState(self::token(), self::patient(), new AgentDocumentId(str_repeat('e', 128)));
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame(BriefFailure::AgentUnavailable, $e->failure);
            $this->assertSame(self::TRACE, $e->correlationId);
        }
    }

    /**
     * @param \Closure(): mixed $call
     */
    private function expectFailure(BriefFailure $expected, \Closure $call): void
    {
        try {
            $call();
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame($expected, $e->failure);
        }
    }

    /**
     * @return array<string, mixed>
     */
    private static function askBody(string $sessionKey): array
    {
        return [
            'status' => 'answered',
            'reason_code' => 'answered',
            'message' => "Potassium 5.9 mmol/L (flag H printed).\n\nSources read for this question: the chart.",
            'answer_view' => ['schema_version' => 'w2-ui/answer-view/1.0.0', 'answer' => ['answer' => 'x']],
            'unverified' => [],
            'critical_values' => [],
            'discrepancies' => [],
            'limitations' => ['guideline_applicability_not_verified'],
            'gaps' => [],
            'routing' => [['route' => 'answer', 'reason' => 'answered']],
            'handoffs' => [],
            'conversation' => ['turn' => 1, 'transition' => 'started', 'context_turns' => 0, 'state_saved' => true],
            'patient_session_key' => $sessionKey,
            'correlation_id' => self::TRACE,
            'node_ms' => [],
            'total_ms' => 812,
        ];
    }

    /**
     * @param array<string, mixed> $body
     */
    private static function json(int $status, array $body): MockResponse
    {
        return new MockResponse(json_encode($body, JSON_THROW_ON_ERROR), ['http_code' => $status]);
    }

    private static function client(MockHttpClient $http): AgentClient
    {
        return new AgentClient($http, new NullLogger(), 'https://agent.example:8443', new CorrelationId(self::TRACE));
    }

    private static function token(): AccessToken
    {
        return new AccessToken('jwt.token.here');
    }

    private static function patient(): PatientUuid
    {
        return new PatientUuid(self::PATIENT);
    }

    private static function session(): ConversationSessionId
    {
        return ConversationSessionId::derive(new CoreSessionId('abcdefghijklmnopqrstuvwxyz'), self::patient(), new ConversationNonce('nonce-12345678'));
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
}
