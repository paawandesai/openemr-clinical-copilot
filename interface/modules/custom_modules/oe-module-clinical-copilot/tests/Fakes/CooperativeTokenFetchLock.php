<?php

/**
 * A TokenFetchLock for exchanges run as Fibers: a contended acquire yields
 * until the holder releases, as GET_LOCK makes a second worker wait.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\TokenFetchLock;

final class CooperativeTokenFetchLock implements TokenFetchLock
{
    /** @var array<string, true> */
    private array $held = [];

    /** @var list<string> Every name acquired, in order. */
    public array $acquired = [];

    /** How many acquires had to wait for another holder. */
    public int $waits = 0;

    public function __construct(private readonly bool $timesOut = false)
    {
    }

    public function acquire(string $name): bool
    {
        if ($this->timesOut) {
            return false;
        }
        if (isset($this->held[$name])) {
            $this->waits++;
            if (\Fiber::getCurrent() === null) {
                // Outside a Fiber nothing else can run to release it.
                return false;
            }
            while ($this->isHeld($name)) {
                \Fiber::suspend();
            }
        }
        $this->held[$name] = true;
        $this->acquired[] = $name;
        return true;
    }

    public function release(string $name): void
    {
        unset($this->held[$name]);
    }

    public function isHeld(string $name): bool
    {
        return isset($this->held[$name]);
    }
}
