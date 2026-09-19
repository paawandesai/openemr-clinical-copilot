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
use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\ClinicianTokenBroker;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\PatientUuid;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;

final class ClinicianTokenBrokerTest extends TestCase
{
    private const BASE = 'http://localhost/oauth2/default';
    private const REDIRECT = 'https://emr.example/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php';
    private const SESSION = 'coresession0123456789abcdef';
    private const PATIENT = 'a2c0e096-23d5-4cf9-ad5b-4895597363a7';

    /** @var list<HttpExchange> */
    private array $requests = [];

    public function testRunsBothAuthorizeHopsThenRedeemsTheCodeWithSecretAndVerifier(): void
    {
        $broker = $this->broker([
            static fn (): MockResponse => new MockResponse('<form>', ['http_code' => 200, 'response_headers' => [
                'Set-Cookie: authserverOpenEMR=first; path=/',
                'Set-Cookie: authserverOpenEMR=oauth42; path=/; HttpOnly',
            ]]),
            self::redirectTo(self::REDIRECT . '?code=the-code&state={state}'),
            static fn (): MockResponse => new MockResponse('{"access_token":"jwt.token.here","token_type":"Bearer","expires_in":3600}', ['http_code' => 200]),
        ]);

        $token = $broker->issueFor(new CoreSessionId(self::SESSION), new PatientUuid(self::PATIENT));

        $this->assertSame('jwt.token.here', $token->value);
        $this->assertCount(3, $this->requests);
        [$first, $second, $redeem] = $this->requests;
        $firstQuery = $first->query();
        $secondQuery = $second->query();

        $this->assertStringStartsWith(self::BASE . '/authorize?', $first->url);
        $this->assertSame('launch-for-' . self::PATIENT, $firstQuery['launch'] ?? null);
        $this->assertSame('https://emr.example/apis/default/fhir', $firstQuery['aud'] ?? null);
        $this->assertSame(ClinicianTokenBroker::SCOPES, $firstQuery['scope'] ?? null);
        $this->assertSame('S256', $firstQuery['code_challenge_method'] ?? null);
        $this->assertArrayNotHasKey('autosubmit', $firstQuery);
        $this->assertContains('Cookie: OpenEMR=' . self::SESSION, $first->headerLines());

        $this->assertSame('1', $secondQuery['autosubmit'] ?? null);
        $this->assertSame($firstQuery['state'] ?? null, $secondQuery['state'] ?? null);
        $this->assertContains('Cookie: OpenEMR=' . self::SESSION . '; authserverOpenEMR=oauth42', $second->headerLines());

        $this->assertSame('POST', $redeem->method);
        $this->assertSame(self::BASE . '/token', $redeem->url);
        $body = $redeem->formBody();
        $this->assertSame('authorization_code', $body['grant_type'] ?? null);
        $this->assertSame('the-code', $body['code'] ?? null);
        $this->assertSame('the-secret', $body['client_secret'] ?? null);
        $this->assertSame(self::REDIRECT, $body['redirect_uri'] ?? null);
        $this->assertSame(
            rtrim(strtr(base64_encode(hash('sha256', $body['code_verifier'] ?? '', true)), '+/', '-_'), '='),
            $firstQuery['code_challenge'] ?? null,
        );
    }

    public function testAConversationTurnAsksForLabAndVisitScopesToo(): void
    {
        $broker = $this->broker([
            self::firstHop(),
            self::redirectTo(self::REDIRECT . '?code=c&state={state}'),
            static fn (): MockResponse => new MockResponse('{"access_token":"t"}', ['http_code' => 200]),
        ]);
        $broker->issueFor(new CoreSessionId(self::SESSION), new PatientUuid(self::PATIENT), ClinicianTokenBroker::CONVERSATION_SCOPES);

        $expected = ClinicianTokenBroker::SCOPES . ' user/Observation.read user/Encounter.read';
        $this->assertSame($expected, $this->requests[0]->query()['scope'] ?? null);
        $this->assertSame($expected, $this->requests[1]->query()['scope'] ?? null);
    }

    public function testSecretIsNeverSentToAuthorize(): void
    {
        $broker = $this->broker([
            self::firstHop(),
            self::redirectTo(self::REDIRECT . '?code=c&state={state}'),
            static fn (): MockResponse => new MockResponse('{"access_token":"t"}', ['http_code' => 200]),
        ]);
        $broker->issueFor(new CoreSessionId(self::SESSION), new PatientUuid(self::PATIENT));

        $this->assertStringNotContainsString('the-secret', $this->requests[0]->url);
        $this->assertStringNotContainsString('the-secret', $this->requests[1]->url);
    }

    /**
     * @param list<\Closure(string, string): MockResponse> $responses
     */
    #[DataProvider('failingExchanges')]
    public function testAnyBreakInTheExchangeIsTokenUnavailable(array $responses, int $expectedRequests): void
    {
        $broker = $this->broker($responses);
        try {
            $broker->issueFor(new CoreSessionId(self::SESSION), new PatientUuid(self::PATIENT));
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame(BriefFailure::TokenUnavailable, $e->failure);
        }
        $this->assertCount($expectedRequests, $this->requests);
    }

    /**
     * @return array<string, array{list<\Closure(string, string): MockResponse>, int}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function failingExchanges(): array
    {
        return [
            'no OAuth session cookie from the first hop' => [
                [static fn (): MockResponse => new MockResponse('', ['http_code' => 200])],
                1,
            ],
            'first hop redirects to the provider login (no live session)' => [
                [static fn (): MockResponse => new MockResponse('', ['http_code' => 307, 'response_headers' => ['Location: /oauth2/default/provider/login']])],
                1,
            ],
            'second hop redirects anywhere but the registered redirect URI' => [
                [self::firstHop(), self::redirectTo('https://attacker.example/cb?code=c&state={state}')],
                2,
            ],
            'second hop returns a state that does not match' => [
                [self::firstHop(), self::redirectTo(self::REDIRECT . '?code=c&state=forged')],
                2,
            ],
            'second hop has no code' => [
                [self::firstHop(), self::redirectTo(self::REDIRECT . '?error=access_denied&state={state}')],
                2,
            ],
            'token endpoint refuses the code' => [
                [self::firstHop(), self::redirectTo(self::REDIRECT . '?code=c&state={state}'), static fn (): MockResponse => new MockResponse('{"error":"invalid_request"}', ['http_code' => 400])],
                3,
            ],
            'token response has no access token' => [
                [self::firstHop(), self::redirectTo(self::REDIRECT . '?code=c&state={state}'), static fn (): MockResponse => new MockResponse('{"token_type":"Bearer"}', ['http_code' => 200])],
                3,
            ],
            'loopback transport failure' => [
                [static fn (): MockResponse => new MockResponse('', ['error' => 'connection refused'])],
                1,
            ],
        ];
    }

    public function testTokenAndSessionAreRedactedFromDumps(): void
    {
        $this->assertStringNotContainsString('secret-jwt', print_r(new AccessToken('secret-jwt'), true));
        $this->assertStringNotContainsString(self::SESSION, print_r(new CoreSessionId(self::SESSION), true));
    }

    /**
     * @return \Closure(string, string): MockResponse
     */
    private static function firstHop(): \Closure
    {
        return static fn (): MockResponse => new MockResponse('', ['http_code' => 200, 'response_headers' => ['Set-Cookie: authserverOpenEMR=oauth42']]);
    }

    /**
     * A 302 to $location, with {state} replaced by the state the request sent.
     *
     * @return \Closure(string, string): MockResponse
     */
    private static function redirectTo(string $location): \Closure
    {
        return static function (string $method, string $url) use ($location): MockResponse {
            $state = HttpExchange::stringPairs((string) parse_url($url, PHP_URL_QUERY))['state'] ?? '';
            return new MockResponse('', ['http_code' => 302, 'response_headers' => ['Location: ' . str_replace('{state}', $state, $location)]]);
        };
    }

    /**
     * @param list<\Closure(string, string): MockResponse> $responses
     */
    private function broker(array $responses): ClinicianTokenBroker
    {
        $this->requests = [];
        $http = new MockHttpClient(function (string $method, string $url, array $options) use (&$responses): MockResponse {
            $this->requests[] = new HttpExchange($method, $url, $options);
            $next = array_shift($responses);
            if ($next === null) {
                $this->fail('Unexpected request to ' . $url);
            }
            return $next($method, $url);
        });
        return new ClinicianTokenBroker(
            $http,
            new NullLogger(),
            static fn (PatientUuid $patient): string => 'launch-for-' . $patient->value,
            self::BASE,
            'https://emr.example/apis/default/fhir',
            'client-id',
            'the-secret',
            self::REDIRECT,
        );
    }
}
