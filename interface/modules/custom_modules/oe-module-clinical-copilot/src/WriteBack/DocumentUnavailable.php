<?php

/**
 * A source-document read that will not be served.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use RuntimeException;

final class DocumentUnavailable extends RuntimeException
{
    public function __construct(public readonly DocumentFailure $failure)
    {
        parent::__construct($failure->value);
    }
}
