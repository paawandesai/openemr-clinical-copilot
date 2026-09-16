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

use OpenEMR\Common\Crypto\CryptoInterface;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\ClinicalCopilot\ModuleConfig;
use OpenEMR\Modules\ClinicalCopilot\ModuleNotConfigured;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class ModuleConfigTest extends TestCase
{
    public function testParsesACompleteConfiguration(): void
    {
        $config = ModuleConfig::fromGlobals(new OEGlobalsBag(self::valid()), $this->crypto());

        $this->assertSame('https://agent.example:8443', $config->agentUrl);
        $this->assertNull($config->agentCaFile);
        $this->assertSame('client-id', $config->clientId);
        $this->assertSame('decrypted-secret', $config->clientSecret);
        $this->assertSame('https://emr.example/cb.php', $config->redirectUri);
        $this->assertSame('http://localhost', $config->loopbackUrl);
    }

    public function testLoopbackDefaultsToLocalhost(): void
    {
        $values = self::valid();
        unset($values[ModuleConfig::LOOPBACK_URL]);
        $this->assertSame('http://localhost', ModuleConfig::fromGlobals(new OEGlobalsBag($values), $this->crypto())->loopbackUrl);
    }

    /**
     * @param array<string, string> $override
     */
    #[DataProvider('invalidSettings')]
    public function testRejectsInvalidSettingsNamingTheSetting(array $override, string $setting): void
    {
        try {
            ModuleConfig::fromGlobals(new OEGlobalsBag(array_merge(self::valid(), $override)), $this->crypto());
            $this->fail('Expected ModuleNotConfigured');
        } catch (ModuleNotConfigured $e) {
            $this->assertSame($setting, $e->setting);
        }
    }

    /**
     * @return array<string, array{array<string, string>, string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function invalidSettings(): array
    {
        return [
            'agent over plain http' => [[ModuleConfig::AGENT_URL => 'http://agent.example:8443'], ModuleConfig::AGENT_URL],
            'agent url empty' => [[ModuleConfig::AGENT_URL => ''], ModuleConfig::AGENT_URL],
            'agent url with credentials' => [[ModuleConfig::AGENT_URL => 'https://u:p@agent.example'], ModuleConfig::AGENT_URL],
            'CA file that does not exist' => [[ModuleConfig::AGENT_CA_FILE => '/nonexistent/ca.pem'], ModuleConfig::AGENT_CA_FILE],
            'client id empty' => [[ModuleConfig::CLIENT_ID => '  '], ModuleConfig::CLIENT_ID],
            'client secret empty' => [[ModuleConfig::CLIENT_SECRET => ''], ModuleConfig::CLIENT_SECRET],
            'client secret that does not decrypt' => [[ModuleConfig::CLIENT_SECRET => 'garbage'], ModuleConfig::CLIENT_SECRET],
            'redirect uri with a query' => [[ModuleConfig::REDIRECT_URI => 'https://emr.example/cb.php?x=1'], ModuleConfig::REDIRECT_URI],
            'loopback pointing off this server' => [[ModuleConfig::LOOPBACK_URL => 'http://emr.example'], ModuleConfig::LOOPBACK_URL],
            'loopback with a path' => [[ModuleConfig::LOOPBACK_URL => 'http://localhost/openemr'], ModuleConfig::LOOPBACK_URL],
            'loopback over another scheme' => [[ModuleConfig::LOOPBACK_URL => 'ftp://localhost'], ModuleConfig::LOOPBACK_URL],
        ];
    }

    /**
     * @return array<string, string>
     */
    private static function valid(): array
    {
        return [
            ModuleConfig::AGENT_URL => 'https://agent.example:8443/',
            ModuleConfig::AGENT_CA_FILE => '',
            ModuleConfig::CLIENT_ID => 'client-id',
            ModuleConfig::CLIENT_SECRET => 'encrypted-secret',
            ModuleConfig::REDIRECT_URI => 'https://emr.example/cb.php',
            ModuleConfig::LOOPBACK_URL => 'http://localhost',
        ];
    }

    private function crypto(): CryptoInterface
    {
        $crypto = $this->createStub(CryptoInterface::class);
        $crypto->method('decryptFromDatabase')->willReturnCallback(
            static fn (?string $value): string => $value === 'encrypted-secret' ? 'decrypted-secret' : throw new \RuntimeException('bad ciphertext'),
        );
        return $crypto;
    }
}
