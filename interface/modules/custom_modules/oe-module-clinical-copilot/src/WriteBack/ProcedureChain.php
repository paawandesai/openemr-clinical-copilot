<?php

/**
 * Writes the procedure_order -> ... -> procedure_result chain.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

interface ProcedureChain
{
    /**
     * Writes one `procedure_order` for the whole panel, then one
     * `procedure_order_code` + `procedure_report` + `procedure_result` per
     * result, at sequence 1..N in the order given.
     *
     * @param list<LabWriteBackResult> $results non-empty
     * @return list<ProcedureChainIds> one per result, in the same order, all
     *                                 sharing the one order id
     */
    public function write(array $results, int $pid, int $userId, int $documentId): array;
}
