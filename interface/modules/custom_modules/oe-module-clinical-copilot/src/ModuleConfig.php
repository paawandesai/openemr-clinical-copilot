<?php

/**
 * Clinical Co-Pilot settings, parsed from OpenEMR globals
 * (Administration > Config > Clinical Co-Pilot).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Common\Crypto\CryptoInterface;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Services\Globals\GlobalSetting;

final readonly class ModuleConfig
{
    public const AGENT_URL = 'clinical_copilot_agent_url';
    public const AGENT_CA_FILE = 'clinical_copilot_agent_ca_file';
    public const CLIENT_ID = 'clinical_copilot_oauth_client_id';
    public const CLIENT_SECRET = 'clinical_copilot_oauth_client_secret';
    public const REDIRECT_URI = 'clinical_copilot_oauth_redirect_uri';
    public const LOOPBACK_URL = 'clinical_copilot_oauth_loopback_url';

    private const DEFAULT_LOOPBACK_URL = 'http://localhost';

    /**
     * The token broker sends the clinician's session cookie and the client
     * secret to this address, so it may only ever name this server.
     */
    private const LOOPBACK_HOSTS = ['localhost', '127.0.0.1', '[::1]'];

    private function __construct(
        public string $agentUrl,
        public ?string $agentCaFile,
        public string $clientId,
        #[\SensitiveParameter] public string $clientSecret,
        public string $redirectUri,
        public string $loopbackUrl,
    ) {
    }

    /**
     * @return array<string, array{title: literal-string, type: string, default: string, description: literal-string}>
     */
    public static function settingDefinitions(): array
    {
        return [
            self::AGENT_URL => [
                'title' => 'Agent base URL',
                'type' => GlobalSetting::DATA_TYPE_TEXT,
                'default' => '',
                'description' => 'HTTPS base URL of the Clinical Co-Pilot agent, without a trailing slash. The Pre-Visit Brief is requested from its /pre-visit-brief endpoint by this server, never by the browser.',
            ],
            self::AGENT_CA_FILE => [
                'title' => 'Agent CA certificate file',
                'type' => GlobalSetting::DATA_TYPE_TEXT,
                'default' => '',
                'description' => 'Absolute path to a PEM file holding the CA certificate that signed the agent TLS certificate. Leave empty to use the system trust store. Certificate verification is never disabled.',
            ],
            self::CLIENT_ID => [
                'title' => 'OAuth client ID',
                'type' => GlobalSetting::DATA_TYPE_TEXT,
                'default' => '',
                'description' => 'Client ID of the confidential API client registered for this module, enabled, with Skip EHR Launch Authorization Flow turned on.',
            ],
            self::CLIENT_SECRET => [
                'title' => 'OAuth client secret',
                'type' => GlobalSetting::DATA_TYPE_ENCRYPTED,
                'default' => '',
                'description' => 'Client secret of that API client. Stored encrypted.',
            ],
            self::REDIRECT_URI => [
                'title' => 'OAuth redirect URI',
                'type' => GlobalSetting::DATA_TYPE_TEXT,
                'default' => '',
                'description' => 'The redirect URI registered for that API client, exactly as registered.',
            ],
            self::LOOPBACK_URL => [
                'title' => 'OAuth loopback base URL',
                'type' => GlobalSetting::DATA_TYPE_TEXT,
                'default' => self::DEFAULT_LOOPBACK_URL,
                'description' => 'Scheme and host this server uses to reach its own OAuth2 endpoints. Must be localhost, 127.0.0.1 or [::1].',
            ],
        ];
    }

    /**
     * @throws ModuleNotConfigured naming the first missing or invalid setting.
     */
    public static function fromGlobals(OEGlobalsBag $globals, CryptoInterface $crypto): self
    {
        $agentUrl = self::parseUrl($globals->getString(self::AGENT_URL), ['https'], self::AGENT_URL);
        $caFile = trim($globals->getString(self::AGENT_CA_FILE));
        if ($caFile !== '' && !is_readable($caFile)) {
            throw new ModuleNotConfigured(self::AGENT_CA_FILE);
        }

        $clientId = trim($globals->getString(self::CLIENT_ID));
        if ($clientId === '') {
            throw new ModuleNotConfigured(self::CLIENT_ID);
        }
        $clientSecret = self::decryptSecret($globals->getString(self::CLIENT_SECRET), $crypto);

        $redirectUri = self::parseUrl($globals->getString(self::REDIRECT_URI), ['https'], self::REDIRECT_URI);

        $loopbackSetting = trim($globals->getString(self::LOOPBACK_URL));
        $loopbackUrl = self::parseUrl(
            $loopbackSetting === '' ? self::DEFAULT_LOOPBACK_URL : $loopbackSetting,
            ['http', 'https'],
            self::LOOPBACK_URL,
        );
        $loopbackParts = parse_url($loopbackUrl);
        if (
            !is_array($loopbackParts)
            || !in_array(strtolower($loopbackParts['host'] ?? ''), self::LOOPBACK_HOSTS, true)
            || ($loopbackParts['path'] ?? '') !== ''
        ) {
            throw new ModuleNotConfigured(self::LOOPBACK_URL);
        }

        return new self(
            $agentUrl,
            $caFile === '' ? null : $caFile,
            $clientId,
            $clientSecret,
            $redirectUri,
            $loopbackUrl,
        );
    }

    /**
     * An absolute URL with one of the allowed schemes and a host, no
     * credentials, query or fragment; returned without a trailing slash.
     *
     * @param list<string> $schemes
     */
    private static function parseUrl(string $value, array $schemes, string $setting): string
    {
        $url = rtrim(trim($value), '/');
        $parts = parse_url($url);
        if (
            $url === ''
            || !is_array($parts)
            || !in_array(strtolower($parts['scheme'] ?? ''), $schemes, true)
            || ($parts['host'] ?? '') === ''
            || isset($parts['user'])
            || isset($parts['pass'])
            || isset($parts['query'])
            || isset($parts['fragment'])
        ) {
            throw new ModuleNotConfigured($setting);
        }
        return $url;
    }

    private static function decryptSecret(string $stored, CryptoInterface $crypto): string
    {
        if ($stored === '') {
            throw new ModuleNotConfigured(self::CLIENT_SECRET);
        }
        try {
            $secret = $crypto->decryptFromDatabase($stored);
        } catch (\Exception $e) {
            throw new ModuleNotConfigured(self::CLIENT_SECRET, $e);
        }
        if ($secret === '') {
            throw new ModuleNotConfigured(self::CLIENT_SECRET);
        }
        return $secret;
    }
}
