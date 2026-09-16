<?php

/**
 * The id of the clinician's logged-in OpenEMR session. The token broker
 * presents it to this server's own authorize endpoint, the way the browser
 * would during an EHR launch. It is a credential: never log or echo it.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final readonly class CoreSessionId
{
    public function __construct(#[\SensitiveParameter] public string $value)
    {
        // PHP session ids: letters, digits, comma and hyphen (session.sid_bits_per_character).
        if (preg_match('/^[A-Za-z0-9,-]{22,256}$/', $value) !== 1) {
            throw new \DomainException('Not a PHP session id');
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
