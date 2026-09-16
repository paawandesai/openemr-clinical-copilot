<?php

/**
 * A Clinical Co-Pilot setting is missing or invalid.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final class ModuleNotConfigured extends \RuntimeException
{
    /**
     * @param string $setting The global setting key. Never its value: one of them is a secret.
     */
    public function __construct(public readonly string $setting, ?\Throwable $previous = null)
    {
        parent::__construct('Clinical Co-Pilot setting is missing or invalid', 0, $previous);
    }
}
