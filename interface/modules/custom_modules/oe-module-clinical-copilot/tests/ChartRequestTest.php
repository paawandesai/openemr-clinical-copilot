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

use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\ChartRequest;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\Request;

final class ChartRequestTest extends TestCase
{
    public function testAcceptsThePatientOpenInTheSession(): void
    {
        $chart = ChartRequest::fromRequest(self::post(['pid' => '49', 'csrf_token_form' => 'good']), '49', self::csrf(...), static fn (): bool => true);

        $this->assertSame(49, $chart->pid);
    }

    /**
     * @param array<string, string> $form
     */
    #[DataProvider('rejectedRequests')]
    public function testRejects(string $method, array $form, mixed $sessionPid, bool $mayReadRecord, BriefFailure $expected): void
    {
        $request = Request::create('/conversation.php', $method, $form);
        try {
            ChartRequest::fromRequest($request, $sessionPid, self::csrf(...), static fn (): bool => $mayReadRecord);
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame($expected, $e->failure);
        }
    }

    /**
     * @return array<string, array{string, array<string, string>, mixed, bool, BriefFailure}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function rejectedRequests(): array
    {
        $form = ['pid' => '49', 'csrf_token_form' => 'good'];
        return [
            'GET' => ['GET', $form, '49', true, BriefFailure::InvalidRequest],
            'wrong CSRF token' => ['POST', ['pid' => '49', 'csrf_token_form' => 'bad'], '49', true, BriefFailure::InvalidRequest],
            'no CSRF token' => ['POST', ['pid' => '49'], '49', true, BriefFailure::InvalidRequest],
            'pid not a number' => ['POST', ['pid' => '49 OR 1=1', 'csrf_token_form' => 'good'], '49', true, BriefFailure::InvalidRequest],
            'pid zero' => ['POST', ['pid' => '0', 'csrf_token_form' => 'good'], '0', true, BriefFailure::InvalidRequest],
            'no medical-record access' => ['POST', $form, '49', false, BriefFailure::Forbidden],
            'another patient is open in the session' => ['POST', $form, '1', true, BriefFailure::PatientContextChanged],
            'another patient is open, pid held as an int' => ['POST', $form, 1, true, BriefFailure::PatientContextChanged],
            'no patient is open in the session' => ['POST', $form, null, true, BriefFailure::PatientContextChanged],
            'session pid is not a patient id' => ['POST', $form, '', true, BriefFailure::PatientContextChanged],
        ];
    }

    public function testTheAclIsNotConsultedForARequestThatFailsCsrf(): void
    {
        $consulted = false;
        try {
            ChartRequest::fromRequest(self::post(['pid' => '49', 'csrf_token_form' => 'bad']), '49', self::csrf(...), static function () use (&$consulted): bool {
                $consulted = true;
                return true;
            });
        } catch (BriefUnavailable) {
        }

        $this->assertFalse($consulted);
    }

    /**
     * @param array<string, string> $form
     */
    private static function post(array $form): Request
    {
        return Request::create('/conversation.php', 'POST', $form);
    }

    private static function csrf(string $token): bool
    {
        return $token === 'good';
    }
}
