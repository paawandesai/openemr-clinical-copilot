<?php

/**
 * A ProcedureChain that hands back predictable ids, or fails on demand.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests\Fakes;

use OpenEMR\Modules\ClinicalCopilot\WriteBack\ProcedureChain;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\ProcedureChainIds;
use Throwable;

final class ScriptedProcedureChain implements ProcedureChain
{
    public int $calls = 0;

    /** @var list<int> how many results each call was given */
    public array $batchSizes = [];

    public function __construct(private ?Throwable $failWith = null, private int $orderId = 8198)
    {
    }

    /**
     * @param list<\OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackResult> $results
     * @return list<ProcedureChainIds>
     */
    public function write(array $results, int $pid, int $userId, int $documentId): array
    {
        $this->calls++;
        $this->batchSizes[] = count($results);
        if ($this->failWith !== null) {
            throw $this->failWith;
        }
        $written = [];
        foreach (array_keys($results) as $index) {
            $written[] = new ProcedureChainIds($this->orderId, 762 + $index, 14849 + $index);
        }
        return $written;
    }
}
