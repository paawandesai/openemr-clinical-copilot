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

use OpenEMR\Common\Database\SqlQueryException;
use OpenEMR\Modules\ClinicalCopilot\DatabaseTokenFetchLock;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class DatabaseTokenFetchLockTest extends TestCase
{
    /** @var list<array{string, list<int|string>}> */
    private array $queries = [];

    public function testAcquireWaitsOnANamedAdvisoryLock(): void
    {
        $this->assertTrue($this->lock(1)->acquire('openemr.clinical_copilot.token.abc'));

        $this->assertSame(
            [['SELECT GET_LOCK(?, ?) AS v', ['openemr.clinical_copilot.token.abc', DatabaseTokenFetchLock::WAIT_SECONDS]]],
            $this->queries,
        );
        $this->assertGreaterThanOrEqual(15, DatabaseTokenFetchLock::WAIT_SECONDS, 'no shorter than one exchange at its slowest');
    }

    public function testANumericStringFromTheDriverIsAnAcquire(): void
    {
        $this->assertTrue($this->lock('1')->acquire('n'));
    }

    #[DataProvider('notAcquired')]
    public function testAnythingButOneIsNotAnAcquire(mixed $returned): void
    {
        $this->assertFalse($this->lock($returned)->acquire('n'));
    }

    /**
     * @return array<string, array{mixed}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function notAcquired(): array
    {
        return [
            'timed out' => [0],
            'timed out, as a string' => ['0'],
            'error' => [null],
        ];
    }

    public function testAQueryFailureIsNotAnAcquire(): void
    {
        $lock = new DatabaseTokenFetchLock(static function (): never {
            throw new SqlQueryException('SELECT GET_LOCK(?, ?) AS v', 'gone away');
        });

        $this->assertFalse($lock->acquire('n'));
    }

    public function testReleaseFreesTheSameName(): void
    {
        $this->lock(1)->release('openemr.clinical_copilot.token.abc');

        $this->assertSame([['SELECT RELEASE_LOCK(?) AS v', ['openemr.clinical_copilot.token.abc']]], $this->queries);
    }

    public function testAReleaseFailureIsSwallowed(): void
    {
        $lock = new DatabaseTokenFetchLock(static function (): never {
            throw new SqlQueryException('SELECT RELEASE_LOCK(?) AS v', 'gone away');
        });

        $lock->release('n');
        $this->addToAssertionCount(1);
    }

    private function lock(mixed $returns): DatabaseTokenFetchLock
    {
        $this->queries = [];
        return new DatabaseTokenFetchLock(function (string $sql, array $binds) use ($returns): mixed {
            $this->queries[] = [$sql, $binds];
            return $returns;
        });
    }
}
