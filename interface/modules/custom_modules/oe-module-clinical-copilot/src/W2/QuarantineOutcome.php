<?php

/**
 * What became of an upload whose extraction found another patient's name or
 * date of birth on it (D60): the card says which.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

enum QuarantineOutcome: string
{
    /** In the quarantine category now, whether this request moved it or an earlier one did. */
    case Quarantined = 'quarantined';

    /** No undeleted copy of these bytes is filed on this chart, so there is nothing to move. */
    case NotOnChart = 'not_on_chart';

    /** It could not be moved: no quarantine category, or no lock. It is still where it was filed. */
    case Failed = 'failed';
}
