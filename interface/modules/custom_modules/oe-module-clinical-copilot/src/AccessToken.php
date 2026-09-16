<?php

/**
 * An OpenEMR OAuth2 access token issued to the clinician for this module's
 * client. Lives only for the request that obtained it: never stored, logged
 * or sent to the browser.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final readonly class AccessToken
{
    public function __construct(#[\SensitiveParameter] public string $value)
    {
        if ($value === '') {
            throw new \DomainException('Access token must not be empty');
        }
    }

    /**
     * @return array<string, string>
     */
    public function __debugInfo(): array
    {
        return ['value' => '[redacted]'];
    }
}
