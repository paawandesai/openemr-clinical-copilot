<?php

/**
 * The ids the procedure chain insert produced.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

/**
 * D1 makes returning real ids part of the decision rather than a nicety:
 * `POST /api/patient/:pid/document` answers with the literal body `true`,
 * so a caller has to re-query the listing to learn what it just created,
 * and two concurrent uploads make that guess wrong.
 */
final readonly class ProcedureChainIds
{
    public function __construct(
        public int $procedureOrderId,
        public int $procedureReportId,
        public int $procedureResultId,
    ) {
    }
}
