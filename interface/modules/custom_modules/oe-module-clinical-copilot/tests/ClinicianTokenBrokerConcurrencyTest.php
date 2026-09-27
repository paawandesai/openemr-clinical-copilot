<?php

/**
 * Concurrent token exchanges for one clinician (DECISIONS.md D64
 * (token fetch race)). Opening a chart fires the brief, the D60 reconcile
 * and, when a file is chosen, the upload at once; each brokers its own
 * token, and OpenEMR keeps one trusted-user row per (client, user) that each
 * authorize overwrites. Each exchange here runs as a Fiber that yields at
 * every HTTP request, so the three interleave the way PHP workers do.
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
use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\ClinicianTokenBroker;
use OpenEMR\Modules\ClinicalCopilot\CoreSessionId;
use OpenEMR\Modules\ClinicalCopilot\PatientUuid;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\CooperativeTokenFetchLock;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\SharedRowOAuthServer;
use OpenEMR\Modules\ClinicalCopilot\TokenFetchLock;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Contracts\HttpClient\HttpClientInterface;

final class ClinicianTokenBrokerConcurrencyTest extends TestCase
{
    private const CLIENT = 'client-id';
    private const REDIRECT = 'https://emr.example/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php';
    private const PATIENT = 'a2c0e096-23d5-4cf9-ad5b-4895597363a7';
    private const TAB = 'coresession-tab-0123456789';
    private const OTHER_TAB = 'coresession-tab-9876543210';
    private const COLLEAGUE = 'coresession-colleague-0123';

    private SharedRowOAuthServer $server;

    protected function setUp(): void
    {
        $this->server = new SharedRowOAuthServer(self::CLIENT, self::REDIRECT, [
            self::TAB => 'user-uuid-7',
            self::OTHER_TAB => 'user-uuid-7',
            self::COLLEAGUE => 'user-uuid-9',
        ]);
    }

    public function testTheThreeRequestsOfOneChartOpenEachGetAToken(): void
    {
        $lock = new CooperativeTokenFetchLock();
        $broker = $this->broker($lock, '7');

        [$brief, $upload, $reconcile] = self::concurrently([
            static fn (): AccessToken => $broker->issueFor(new CoreSessionId(self::TAB), new PatientUuid(self::PATIENT)),
            static fn (): AccessToken => $broker->issueFor(new CoreSessionId(self::TAB), new PatientUuid(self::PATIENT), ClinicianTokenBroker::LAB_UPLOAD_SCOPES),
            static fn (): AccessToken => $broker->issueFor(new CoreSessionId(self::TAB), new PatientUuid(self::PATIENT)),
        ]);

        $this->assertInstanceOf(AccessToken::class, $brief);
        $this->assertInstanceOf(AccessToken::class, $upload);
        $this->assertInstanceOf(AccessToken::class, $reconcile);
        $this->assertSame(0, $this->server->refused);
        $this->assertSame(2, $lock->waits, 'the second and third exchange waited for the first');
    }

    public function testTwoTabsOfTheSameClinicianAreSerializedToo(): void
    {
        // One trusted-user row per user, not per session: a second tab or
        // browser races the first exactly as the card's own requests do.
        $lock = new CooperativeTokenFetchLock();

        [$first, $second] = self::concurrently([
            fn (): AccessToken => $this->broker($lock, '7')->issueFor(new CoreSessionId(self::TAB), new PatientUuid(self::PATIENT)),
            fn (): AccessToken => $this->broker($lock, '7')->issueFor(new CoreSessionId(self::OTHER_TAB), new PatientUuid(self::PATIENT)),
        ]);

        $this->assertInstanceOf(AccessToken::class, $first);
        $this->assertInstanceOf(AccessToken::class, $second);
        $this->assertSame(0, $this->server->refused);
    }

    public function testDifferentCliniciansDoNotWaitForEachOther(): void
    {
        $lock = new CooperativeTokenFetchLock();

        [$mine, $theirs] = self::concurrently([
            fn (): AccessToken => $this->broker($lock, '7')->issueFor(new CoreSessionId(self::TAB), new PatientUuid(self::PATIENT)),
            fn (): AccessToken => $this->broker($lock, '9')->issueFor(new CoreSessionId(self::COLLEAGUE), new PatientUuid(self::PATIENT)),
        ]);

        $this->assertInstanceOf(AccessToken::class, $mine);
        $this->assertInstanceOf(AccessToken::class, $theirs);
        $this->assertSame(0, $lock->waits);
        $this->assertCount(2, array_unique($lock->acquired));
    }

    public function testTheLockIsReleasedWhenTheExchangeFails(): void
    {
        $lock = new CooperativeTokenFetchLock();
        $broker = $this->broker($lock, '7');

        try {
            // A session the server does not know: redirected to the login page.
            $broker->issueFor(new CoreSessionId('coresession-expired-000000'), new PatientUuid(self::PATIENT));
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame(BriefFailure::TokenUnavailable, $e->failure);
        }

        $this->assertSame([ClinicianTokenBroker::lockName(self::CLIENT, '7')], $lock->acquired);
        $this->assertFalse($lock->isHeld(ClinicianTokenBroker::lockName(self::CLIENT, '7')));
        $this->assertStringStartsWith('token-for-', $broker->issueFor(new CoreSessionId(self::TAB), new PatientUuid(self::PATIENT))->value);
    }

    public function testALockNotObtainedInTimeIsTokenUnavailableAndSendsNothing(): void
    {
        $sent = 0;
        $http = new MockHttpClient(static function () use (&$sent): never {
            $sent++;
            throw new \LogicException('No request may be sent without the lock');
        });
        $broker = $this->broker(new CooperativeTokenFetchLock(timesOut: true), '7', $http);

        try {
            $broker->issueFor(new CoreSessionId(self::TAB), new PatientUuid(self::PATIENT));
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame(BriefFailure::TokenUnavailable, $e->failure);
        }
        $this->assertSame(0, $sent);
    }

    public function testTheLockNameIsPerClientAndUserAndCarriesNeither(): void
    {
        $name = ClinicianTokenBroker::lockName('AMPCwe6N_client', '42');

        $this->assertSame($name, ClinicianTokenBroker::lockName('AMPCwe6N_client', '42'));
        $this->assertNotSame($name, ClinicianTokenBroker::lockName('AMPCwe6N_client', '43'));
        $this->assertNotSame($name, ClinicianTokenBroker::lockName('other_client', '42'));
        // A user id that ends where a client id could begin is still a different pair.
        $this->assertNotSame(ClinicianTokenBroker::lockName('ab', 'c'), ClinicianTokenBroker::lockName('a', 'bc'));
        $this->assertLessThanOrEqual(64, strlen($name), 'MySQL and MariaDB cap GET_LOCK names at 64 characters');
        $this->assertStringStartsWith('openemr.', $name);
        $this->assertStringNotContainsString('AMPCwe6N', $name);
    }

    /**
     * Runs each closure as a Fiber, round robin, until all have finished.
     *
     * @param list<\Closure(): AccessToken> $flows
     * @return list<AccessToken|BriefUnavailable>
     */
    private static function concurrently(array $flows): array
    {
        $results = [];
        $fibers = [];
        foreach ($flows as $i => $flow) {
            $fibers[$i] = new \Fiber(static function () use ($flow, &$results, $i): void {
                try {
                    $results[$i] = $flow();
                } catch (BriefUnavailable $e) {
                    $results[$i] = $e;
                }
            });
        }
        foreach ($fibers as $fiber) {
            $fiber->start();
        }
        for ($round = 0; array_filter($fibers, static fn (\Fiber $f): bool => !$f->isTerminated()) !== []; $round++) {
            if ($round > 1000) {
                throw new \LogicException('The exchanges did not finish: a lock was never released');
            }
            foreach ($fibers as $fiber) {
                if ($fiber->isSuspended()) {
                    $fiber->resume();
                }
            }
        }
        ksort($results);
        return array_values($results);
    }

    private function broker(TokenFetchLock $lock, string $user, ?HttpClientInterface $http = null): ClinicianTokenBroker
    {
        return new ClinicianTokenBroker(
            $http ?? $this->server->http(),
            new NullLogger(),
            static fn (PatientUuid $patient): string => 'launch-for-' . $patient->value,
            'http://localhost/oauth2/default',
            'https://emr.example/apis/default/fhir',
            self::CLIENT,
            'the-secret',
            self::REDIRECT,
            $lock,
            $user,
        );
    }
}
