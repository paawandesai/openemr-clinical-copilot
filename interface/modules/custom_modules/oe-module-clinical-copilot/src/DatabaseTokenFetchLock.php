<?php

/**
 * The token fetch lock as a database advisory lock (GET_LOCK), the way
 * OpenEMR's BackgroundServiceRunner takes its orchestrator lock.
 *
 * It holds across PHP-FPM workers, and across web containers sharing the
 * database, and it is released when the connection closes, so a worker that
 * dies holding it does not wedge the clinician's next chart open. The
 * loopback requests the broker makes while holding it run in other workers
 * and never take it, and the core session is opened read-and-close, so a
 * waiter holds nothing the holder needs.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Database\SqlQueryException;

final readonly class DatabaseTokenFetchLock implements TokenFetchLock
{
    /**
     * One exchange at its slowest (three requests of up to 5 s each). An
     * exchange normally takes well under a second, so a chart open's handful
     * of queued requests wait a second or two between them.
     */
    public const WAIT_SECONDS = 15;

    /**
     * @param \Closure(string, list<int|string>): mixed $selectOne Runs one SELECT and returns its only column, `v`; self::viaQueryUtils() outside tests.
     */
    public function __construct(private \Closure $selectOne)
    {
    }

    public static function viaQueryUtils(): self
    {
        return new self(static function (string $sql, array $binds): mixed {
            $row = QueryUtils::querySingleRow($sql, $binds, false);
            return is_array($row) ? ($row['v'] ?? null) : null;
        });
    }

    public function acquire(string $name): bool
    {
        try {
            $got = ($this->selectOne)('SELECT GET_LOCK(?, ?) AS v', [$name, self::WAIT_SECONDS]);
        } catch (SqlQueryException) {
            return false;
        }
        // 1 on acquire, 0 on timeout, NULL on error; ADOdb may hand back either an int or a numeric string.
        return $got === 1 || $got === '1';
    }

    public function release(string $name): void
    {
        try {
            ($this->selectOne)('SELECT RELEASE_LOCK(?) AS v', [$name]);
        } catch (SqlQueryException) {
            // The lock is the connection's, and goes when the request ends.
        }
    }
}
