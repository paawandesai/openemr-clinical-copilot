<?php

/**
 * OpenEMR's authorization server as the token broker meets it, reduced to the
 * part that races: one trusted-user row per (client, user).
 *
 * The auto-submitted authorize writes the code it issues into that row, and
 * the token endpoint accepts a code only if the row still holds it
 * (AuthorizationController::processAuthorizeFlowForLaunch() saves it with
 * TrustedUserService::saveTrustedUser(), an UPDATE of the existing row;
 * ::oauthAuthorizeToken() restores the session by getTrustedUserByCode(),
 * and without it answers 400 "CSRF check failed").
 *
 * Every request is a point where a Fiber-run exchange yields, so a test can
 * interleave several of them the way concurrent PHP workers do.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\Tests\HttpExchange;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;

final class SharedRowOAuthServer
{
    /** @var array<string, string> "client|user" => the code the row holds. */
    private array $rows = [];

    private int $issued = 0;

    /** Redemptions refused because the row no longer held the code. */
    public int $refused = 0;

    /**
     * @param array<string, string> $sessionUsers core session id => the user it is logged in as.
     */
    public function __construct(
        private readonly string $clientId,
        private readonly string $redirectUri,
        private readonly array $sessionUsers,
    ) {
    }

    public function http(): MockHttpClient
    {
        return new MockHttpClient(function (string $method, string $url, array $options): MockResponse {
            if (\Fiber::getCurrent() !== null) {
                \Fiber::suspend();
            }
            $exchange = new HttpExchange($method, $url, $options);
            return str_ends_with((string) parse_url($url, PHP_URL_PATH), '/token')
                ? $this->token($exchange)
                : $this->authorize($exchange);
        });
    }

    private function authorize(HttpExchange $exchange): MockResponse
    {
        $cookies = [];
        foreach ($exchange->headerLines() as $line) {
            if (str_starts_with($line, 'Cookie: ')) {
                $cookies = HttpExchange::stringPairs(str_replace('; ', '&', substr($line, 8)));
            }
        }
        $user = $this->sessionUsers[$cookies['OpenEMR'] ?? ''] ?? null;
        if ($user === null) {
            return new MockResponse('', ['http_code' => 307, 'response_headers' => ['Location: /oauth2/default/provider/login']]);
        }

        $query = $exchange->query();
        if (!isset($query['autosubmit'])) {
            return new MockResponse('<form>', ['http_code' => 200, 'response_headers' => [
                'Set-Cookie: authserverOpenEMR=oauth' . ++$this->issued . '; path=/; HttpOnly',
            ]]);
        }

        $code = 'code-' . ++$this->issued;
        $this->rows[$this->clientId . '|' . $user] = $code;
        return new MockResponse('', ['http_code' => 302, 'response_headers' => [
            'Location: ' . $this->redirectUri . '?code=' . $code . '&state=' . ($query['state'] ?? ''),
        ]]);
    }

    private function token(HttpExchange $exchange): MockResponse
    {
        $code = $exchange->formBody()['code'] ?? '';
        if ($code === '' || !in_array($code, $this->rows, true)) {
            $this->refused++;
            return new MockResponse('{"error":"invalid_request"}', ['http_code' => 400]);
        }
        return new MockResponse('{"access_token":"token-for-' . $code . '"}', ['http_code' => 200]);
    }
}
