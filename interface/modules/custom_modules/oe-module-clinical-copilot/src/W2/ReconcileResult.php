<?php

/**
 * What reconciling a chart's documents with the agent found (D60): how many
 * chart copies were asked about, and how many were moved to quarantine now.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

final readonly class ReconcileResult
{
    /**
     * @param list<int> $movedDocumentIds `documents.id`s this call quarantined; each is audited.
     */
    public function __construct(
        public int $checked = 0,
        public array $movedDocumentIds = [],
        public int $failed = 0,
    ) {
    }

    /**
     * @return array{checked: int, quarantined: int, failed: int}
     */
    public function toPayload(): array
    {
        return [
            'checked' => $this->checked,
            'quarantined' => count($this->movedDocumentIds),
            'failed' => $this->failed,
        ];
    }
}
