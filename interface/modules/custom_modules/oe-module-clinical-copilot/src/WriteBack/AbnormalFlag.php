<?php

/**
 * What a lab result is, relative to its reference range.
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
 * Mirrors the agent-side `AbnormalFlag` (agent/app/models/w2/lab_values.py).
 * Backed, because the value arrives over HTTP and is persisted.
 *
 * Five members, not four: `indeterminate` is a real conclusion about a
 * censored value, and `procedure_result.abnormal` has no column value that
 * means it. `toOpenEmrCode()` therefore refuses to map it rather than
 * writing `no`, which would put a false normal in a chart.
 */
enum AbnormalFlag: string
{
    case NORMAL = 'normal';
    case HIGH = 'high';
    case LOW = 'low';
    case ABNORMAL = 'abnormal';
    case INDETERMINATE = 'indeterminate';

    /**
     * The `proc_res_abnormal` list option id for this flag.
     *
     * OpenEMR maps `procedure_result.abnormal` onto
     * `Observation.interpretation` through a `list_options` join, so the
     * value has to be one of the list's option ids (`no`, `yes`, `high`,
     * `low`) and not free text — see docs/w2/spikes/writeback.md §5.
     *
     * @throws InvalidWriteBackRequest if the flag is `indeterminate`.
     */
    public function toOpenEmrCode(): string
    {
        return match ($this) {
            self::NORMAL => 'no',
            self::HIGH => 'high',
            self::LOW => 'low',
            self::ABNORMAL => 'yes',
            self::INDETERMINATE => throw new InvalidWriteBackRequest(
                'results[0].abnormal',
                'an indeterminate result is not written back: procedure_result.abnormal '
                . 'has no value meaning "undecidable", and writing "no" would record a '
                . 'normal the data does not support'
            ),
        };
    }
}
