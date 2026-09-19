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

use OpenEMR\Modules\ClinicalCopilot\ConversationNonce;
use OpenEMR\Modules\ClinicalCopilot\ConversationSessionId;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\PatientUuid;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class ConversationSessionIdTest extends TestCase
{
    private const LOGIN = 'abcdefghijklmnopqrstuvwxyz012345';
    private const TONJA = 'a2c37430-0a29-4c57-8195-2c924c2b654a';
    private const PHIL = 'a2c36e54-af42-4c38-b86e-ade616ed5494';
    private const NONCE = '0123456789abcdef0123456789abcdef';

    public function testTheSameConversationKeepsItsSessionAcrossTurns(): void
    {
        $this->assertSame(self::derive()->value, self::derive()->value);
    }

    public function testMatchesTheAgentsSessionIdPattern(): void
    {
        // agent/app/models/api.py: SESSION_ID_PATTERN
        $this->assertMatchesRegularExpression('/^[A-Za-z0-9-]{8,64}$/', self::derive()->value);
    }

    /**
     * The patient-switch guarantee: a browser that replays patient A's nonce
     * with patient B's chart open reaches a different agent session.
     */
    public function testTheSameNonceUnderAnotherPatientIsADifferentSession(): void
    {
        $this->assertNotSame(self::derive()->value, self::derive(patient: self::PHIL)->value);
    }

    public function testANewConversationIsADifferentSession(): void
    {
        $this->assertNotSame(self::derive()->value, self::derive(nonce: 'fedcba9876543210fedcba9876543210')->value);
    }

    public function testAnotherLoginCannotResumeTheConversation(): void
    {
        $this->assertNotSame(self::derive()->value, self::derive(login: 'ZYXWVUTSRQPONMLKJIHGFEDCBA987654')->value);
    }

    public function testDoesNotRevealTheLoginSessionId(): void
    {
        $this->assertStringNotContainsString(self::LOGIN, self::derive()->value);
    }

    #[DataProvider('malformedNonces')]
    public function testRejectsAMalformedNonce(string $nonce): void
    {
        $this->expectException(\DomainException::class);
        new ConversationNonce($nonce);
    }

    /**
     * @return array<string, array{string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function malformedNonces(): array
    {
        return [
            'empty' => [''],
            'too short' => ['abc1234'],
            'too long' => [str_repeat('a', 65)],
            'line break' => ["0123456789abcdef\n" . self::PHIL],
            'punctuation' => ['0123456789abcdef;'],
        ];
    }

    private static function derive(string $login = self::LOGIN, string $patient = self::TONJA, string $nonce = self::NONCE): ConversationSessionId
    {
        return ConversationSessionId::derive(new CoreSessionId($login), new PatientUuid($patient), new ConversationNonce($nonce));
    }
}
