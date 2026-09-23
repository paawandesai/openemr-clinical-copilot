<?php

/**
 * The Week 2 card's small values: the correlation id, the agent's document
 * id and the list a question posts, and the GET form of ChartRequest the
 * status poll uses.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests;

use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\ChartRequest;
use OpenEMR\Modules\ClinicalCopilot\CorrelationId;
use OpenEMR\Modules\ClinicalCopilot\W2\AgentDocumentId;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentReadRequest;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\Request;

final class W2ValuesTest extends TestCase
{
    public function testAMintedCorrelationIdIsAVersion4Uuid(): void
    {
        $first = CorrelationId::mint()->value;
        $this->assertMatchesRegularExpression('/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/', $first);
        $this->assertNotSame($first, CorrelationId::mint()->value);
    }

    #[DataProvider('notCorrelationIds')]
    public function testACorrelationIdThatCouldCarryTextIsRefused(string $value): void
    {
        $this->expectException(\DomainException::class);
        new CorrelationId($value);
    }

    /**
     * @return array<string, array{string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function notCorrelationIds(): array
    {
        return [
            'empty' => [''],
            'uppercase' => ['3F2B9C1E-7D4A-4E8B-9F0C-1A2B3C4D5E6F'],
            'a sentence' => ['patient Phil Belford asked about potassium'],
            'a uuid with a tail' => ['3f2b9c1e-7d4a-4e8b-9f0c-1a2b3c4d5e6f x'],
        ];
    }

    public function testADocumentIdIsTheSha3OfTheBytes(): void
    {
        $this->assertSame(hash('sha3-512', 'x'), AgentDocumentId::ofBytes('x')->value);
    }

    public function testTheDocumentListIsBoundedAndUnrepeated(): void
    {
        $a = str_repeat('a', 128);
        $b = str_repeat('b', 128);
        $this->assertSame([], AgentDocumentId::listFrom(null, 4));
        $this->assertSame([$a, $b], array_map(static fn (AgentDocumentId $id): string => $id->value, AgentDocumentId::listFrom([$a, $b], 4)));

        foreach ([[$a, $a], [$a, $b, $a . 'x'], array_fill(0, 5, $a), 'not-a-list', [1]] as $bad) {
            try {
                AgentDocumentId::listFrom($bad, 4);
                $this->fail('Expected DomainException');
            } catch (\DomainException) {
                $this->addToAssertionCount(1);
            }
        }
    }

    public function testTheStatusPollIsCheckedAsAGetInTheSameOrder(): void
    {
        $get = Request::create('/upload-status.php', 'GET', ['pid' => '49', 'csrf_token_form' => 'good']);
        $chart = ChartRequest::fromRequest($get, '49', self::csrf(...), static fn (): bool => true, 'GET');
        $this->assertSame(49, $chart->pid);

        $cases = [
            'a POST to the GET route' => [Request::create('/upload-status.php', 'POST', ['pid' => '49', 'csrf_token_form' => 'good']), '49', true, BriefFailure::InvalidRequest],
            'no CSRF token' => [Request::create('/upload-status.php', 'GET', ['pid' => '49']), '49', true, BriefFailure::InvalidRequest],
            'no documents access' => [$get, '49', false, BriefFailure::Forbidden],
            'another chart is open' => [$get, '50', true, BriefFailure::PatientContextChanged],
        ];
        foreach ($cases as $name => [$request, $sessionPid, $mayRead, $expected]) {
            try {
                ChartRequest::fromRequest($request, $sessionPid, self::csrf(...), static fn (): bool => $mayRead, 'GET');
                $this->fail('Expected BriefUnavailable: ' . $name);
            } catch (BriefUnavailable $e) {
                $this->assertSame($expected, $e->failure, $name);
            }
        }
    }

    public function testThePreviewAcceptsACitationsDigestAsWellAsAnId(): void
    {
        $digest = str_repeat('f', 128);
        $read = DocumentReadRequest::fromRequest(Request::create('/document.php', 'GET', ['document_id' => $digest]), '49', static fn (): bool => true);
        $this->assertNull($read->documentId);
        $this->assertSame($digest, $read->sha3512);
        $this->assertSame(49, $read->pid);

        $byId = DocumentReadRequest::fromRequest(Request::create('/document.php', 'GET', ['document_id' => '2474']), '49', static fn (): bool => true);
        $this->assertSame(2474, $byId->documentId);
        $this->assertNull($byId->sha3512);
    }

    private static function csrf(string $token): bool
    {
        return $token === 'good';
    }
}
