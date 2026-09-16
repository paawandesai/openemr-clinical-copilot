<?php

/**
 * Obtains an OAuth2 access token for the logged-in clinician, from this
 * OpenEMR's own authorization server, without asking them to log in again.
 *
 * This is OpenEMR's SMART EHR launch, run server side. OpenEMR already lets a
 * client flagged "skip EHR launch authorization flow" (with the
 * oauth_ehr_launch_authorization_flow_skip global on) receive an
 * authorization code for whoever holds a live core session, identified by
 * that session's cookie (AuthorizationController::getLoggedInCoreUserUuid()).
 * A browser does it in two hops: authorize answers with an auto-submit page
 * and an OAuth session cookie, and the auto-submitted request, carrying both
 * cookies, is redirected to the client with a code. The broker makes the same
 * two requests over loopback, then redeems the code with the client secret
 * and a PKCE verifier.
 *
 * Doing it server side keeps the token, the code and the client secret out
 * of the browser, and avoids the auto-submit page, which posts to
 * site_addr_oath: an address browsers cannot reach on a deployment where it
 * is still localhost (AUDIT.md F-49).
 *
 * Every authorization decision stays OpenEMR's: the client must be enabled,
 * the redirect URI registered, the launch token decryptable, and the session
 * live; the token carries the scopes the client was registered with, as
 * OpenEMR grants them in this flow.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Common\Session\SessionUtil;
use OpenEMR\FHIR\SMART\SMARTLaunchToken;
use Psr\Log\LoggerInterface;
use Symfony\Contracts\HttpClient\Exception\ExceptionInterface as HttpClientException;
use Symfony\Contracts\HttpClient\HttpClientInterface;
use Symfony\Contracts\HttpClient\ResponseInterface;

final readonly class ClinicianTokenBroker
{
    /** What the agent reads (agent/api-collection/README.md), and nothing else. */
    public const SCOPES = 'openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read';

    /** Each of three loopback requests; they normally take well under a second. */
    private const REQUEST_SECONDS = 5.0;

    /**
     * @param \Closure(PatientUuid): string $launchToken Serializes the EHR launch context; self::smartLaunchToken(...) outside tests.
     * @param string $oauthBaseUrl Loopback OAuth2 base, e.g. http://localhost/oauth2/default.
     * @param string $audience The FHIR base URL as this server advertises it (ServerConfig::getFhirUrl()).
     */
    public function __construct(
        private HttpClientInterface $http,
        private LoggerInterface $logger,
        private \Closure $launchToken,
        private string $oauthBaseUrl,
        private string $audience,
        private string $clientId,
        #[\SensitiveParameter] private string $clientSecret,
        private string $redirectUri,
    ) {
    }

    /**
     * @throws BriefUnavailable TokenUnavailable, whatever the cause.
     */
    public function issueFor(#[\SensitiveParameter] CoreSessionId $session, PatientUuid $patient): AccessToken
    {
        $verifier = self::base64Url(random_bytes(48));
        $state = bin2hex(random_bytes(16));

        $query = [
            'response_type' => 'code',
            'client_id' => $this->clientId,
            'redirect_uri' => $this->redirectUri,
            'scope' => self::SCOPES,
            'state' => $state,
            'aud' => $this->audience,
            'launch' => ($this->launchToken)($patient),
            'code_challenge' => self::base64Url(hash('sha256', $verifier, true)),
            'code_challenge_method' => 'S256',
        ];
        $coreCookie = SessionUtil::CORE_SESSION_ID . '=' . $session->value;

        try {
            // Hop 1: OpenEMR confirms the core session and starts an OAuth session.
            $first = $this->authorize($query, $coreCookie);
            $oauthSession = $first->getStatusCode() === 200 ? self::lastCookie($first, SessionUtil::OAUTH_SESSION_ID) : null;
            if ($oauthSession === null) {
                throw $this->failed('authorize_session', $first->getStatusCode());
            }

            // Hop 2: the auto-submitted request, answered with a redirect carrying the code.
            $second = $this->authorize(
                $query + ['autosubmit' => '1'],
                $coreCookie . '; ' . SessionUtil::OAUTH_SESSION_ID . '=' . $oauthSession,
            );
            $code = $this->codeFromRedirect($second, $state);

            $tokenResponse = $this->http->request('POST', $this->oauthBaseUrl . '/token', [
                'body' => [
                    'grant_type' => 'authorization_code',
                    'code' => $code,
                    'redirect_uri' => $this->redirectUri,
                    'client_id' => $this->clientId,
                    'client_secret' => $this->clientSecret,
                    'code_verifier' => $verifier,
                ],
                'max_redirects' => 0,
                'max_duration' => self::REQUEST_SECONDS,
            ]);
            if ($tokenResponse->getStatusCode() !== 200) {
                throw $this->failed('token', $tokenResponse->getStatusCode());
            }
            $accessToken = $tokenResponse->toArray(false)['access_token'] ?? null;
        } catch (HttpClientException $e) {
            $this->logger->error('Clinical Co-Pilot could not reach the OAuth2 server over loopback', ['exception' => $e]);
            throw new BriefUnavailable(BriefFailure::TokenUnavailable, null, $e);
        }

        if (!is_string($accessToken) || $accessToken === '') {
            throw $this->failed('token_body', 200);
        }
        return new AccessToken($accessToken);
    }

    /**
     * @param array<string, string> $query
     */
    private function authorize(array $query, #[\SensitiveParameter] string $cookieHeader): ResponseInterface
    {
        return $this->http->request('GET', $this->oauthBaseUrl . '/authorize', [
            'query' => $query,
            'headers' => ['Cookie' => $cookieHeader],
            'max_redirects' => 0,
            'max_duration' => self::REQUEST_SECONDS,
        ]);
    }

    private function codeFromRedirect(ResponseInterface $response, string $expectedState): string
    {
        $status = $response->getStatusCode();
        $location = $response->getHeaders(false)['location'][0] ?? '';
        if (!in_array($status, [301, 302, 303, 307], true) || !str_starts_with($location, $this->redirectUri . '?')) {
            // A redirect anywhere else is OpenEMR's login page: no live session.
            throw $this->failed('authorize_code', $status);
        }
        parse_str((string) parse_url($location, PHP_URL_QUERY), $params);
        $code = $params['code'] ?? null;
        $state = $params['state'] ?? null;
        if (!is_string($code) || $code === '' || !is_string($state) || !hash_equals($expectedState, $state)) {
            throw $this->failed('authorize_code', $status);
        }
        return $code;
    }

    private static function lastCookie(ResponseInterface $response, string $name): ?string
    {
        $value = null;
        foreach ($response->getHeaders(false)['set-cookie'] ?? [] as $header) {
            if (preg_match('/^' . preg_quote($name, '/') . '=([A-Za-z0-9,-]+)(;|$)/', $header, $match) === 1) {
                $value = $match[1];
            }
        }
        return $value;
    }

    private function failed(string $stage, int $status): BriefUnavailable
    {
        $this->logger->error('Clinical Co-Pilot did not obtain an access token', [
            'stage' => $stage,
            'http_status' => $status,
            'client_id' => $this->clientId,
        ]);
        return new BriefUnavailable(BriefFailure::TokenUnavailable);
    }

    /**
     * The launch context OpenEMR's own "SMART Enabled Apps" card would send
     * from this dashboard (SmartLaunchController), encrypted with this
     * server's key so authorize accepts it as originating here.
     */
    public static function smartLaunchToken(PatientUuid $patient): string
    {
        $launch = new SMARTLaunchToken($patient->value);
        $launch->setIntent(SMARTLaunchToken::INTENT_PATIENT_DEMOGRAPHICS_DIALOG);
        $serialized = $launch->serialize();
        if (!is_string($serialized)) {
            throw new \UnexpectedValueException('SMART launch token did not serialize to a string');
        }
        return $serialized;
    }

    private static function base64Url(string $bytes): string
    {
        return rtrim(strtr(base64_encode($bytes), '+/', '-_'), '=');
    }
}
