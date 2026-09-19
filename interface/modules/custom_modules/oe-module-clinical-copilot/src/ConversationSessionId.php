<?php

/**
 * The session_id the agent keys a follow-up conversation by
 * (agent/app/models/api.py: ConversationTurnRequest).
 *
 * It is never taken from the browser. It is derived, on every request, from
 * the clinician's login session, the patient whose chart is open and the
 * card's conversation nonce. Two consequences, both by construction:
 *
 *  - The same nonce under another patient's chart is a different session id,
 *    so the card cannot present patient A's conversation with patient B's
 *    chart open, whatever the browser sends. The agent's own reset on a
 *    patient change (F-50) is never relied on from here.
 *  - A conversation cannot outlive the login that started it: the login
 *    session id is the HMAC key, and it is not recoverable from the result,
 *    which the agent logs as part of patient_session_key.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final readonly class ConversationSessionId
{
    /** Hex characters kept: 128 bits, inside the agent's 8-64 character limit. */
    private const LENGTH = 32;

    private function __construct(public string $value)
    {
    }

    public static function derive(
        #[\SensitiveParameter] CoreSessionId $login,
        PatientUuid $patient,
        ConversationNonce $nonce,
    ): self {
        $message = "clinical-copilot-conversation\n" . $patient->value . "\n" . $nonce->value;
        return new self(substr(hash_hmac('sha256', $message, $login->value), 0, self::LENGTH));
    }
}
