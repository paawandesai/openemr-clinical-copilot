<?php

/**
 * An upload as filed in the chart: its `documents.id`, and whether this
 * request stored it or found it already there.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

final readonly class ChartDocument
{
    public function __construct(
        public int $documentId,
        public bool $created,
    ) {
    }
}
