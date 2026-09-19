<?php

/**
 * The browser's name for one follow-up conversation in the chart card: a
 * random value the card makes when it loads and again on "New conversation".
 * It is not the agent's session id and cannot select one on its own
 * (ConversationSessionId::derive()).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final readonly class ConversationNonce
{
    public function __construct(public string $value)
    {
        if (preg_match('/^[A-Za-z0-9-]{8,64}$/', $value) !== 1) {
            throw new \DomainException('Conversation nonce must be 8 to 64 letters, digits or hyphens');
        }
    }
}
