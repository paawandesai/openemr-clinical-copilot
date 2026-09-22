<?php

/**
 * A rectangle on a page of a source document.
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
 * Mirrors the agent-side `BBox` (agent/app/models/w2/citations.py): PDF
 * points, origin top-left, y increasing downward.
 *
 * `x1 >= x0` and `y1 >= y0`, not `>`: a zero-width box is real data — the
 * document factory emits one for every unflagged lab result — so rejecting
 * it would make this unable to describe a normal row.
 *
 * D2 requires provenance on a persisted lab value to reach the region of
 * the page it came from, and this is the only place that region is
 * recorded: `procedure_result.comments` carries an extraction id and
 * nothing else, so without the ledger row a written-back value could not be
 * traced back to its pixels.
 */
final readonly class BBox
{
    public function __construct(
        public int $page,
        public float $x0,
        public float $y0,
        public float $x1,
        public float $y1,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @throws InvalidWriteBackRequest
     */
    public static function fromArray(array $payload, string $field): self
    {
        $box = new self(
            RequestField::positiveInt($payload, 'page', $field . '.page'),
            RequestField::nonNegativeFloat($payload, 'x0', $field . '.x0'),
            RequestField::nonNegativeFloat($payload, 'y0', $field . '.y0'),
            RequestField::nonNegativeFloat($payload, 'x1', $field . '.x1'),
            RequestField::nonNegativeFloat($payload, 'y1', $field . '.y1'),
        );
        RequestField::rejectUnknownKeys($payload, ['page', 'x0', 'y0', 'x1', 'y1'], $field);
        if ($box->x1 < $box->x0) {
            throw new InvalidWriteBackRequest($field, 'bbox x1 is left of x0');
        }
        if ($box->y1 < $box->y0) {
            throw new InvalidWriteBackRequest($field, 'bbox y1 is above y0');
        }
        return $box;
    }
}
