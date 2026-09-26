<?php

/**
 * What the write-back endpoint did with one document or one result.
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
 * Mirrors the agent-side `WriteOutcome`
 * (agent/app/models/w2/writeback.py).
 *
 * `DUPLICATE_IGNORED` is what makes a retry safe: the ids already recorded
 * are returned unchanged and nothing is inserted (CLAUDE.md invariant 6).
 * It is the ledger's answer, and the ledger is keyed on the file.
 *
 * `ALREADY_IN_CHART` and `CONFLICT` are the chart's answer, for the same
 * results arriving in a different file. The first found a result for the
 * same test and collection date/time with the same value and unit, from any
 * source, and wrote nothing; the second found one with a different value or
 * unit, wrote nothing, and reports both values. Neither ever overwrites a
 * chart value.
 */
enum WriteOutcome: string
{
    case CREATED = 'created';
    case DUPLICATE_IGNORED = 'duplicate_ignored';
    case ALREADY_IN_CHART = 'already_in_chart';
    case CONFLICT = 'conflict';
}
