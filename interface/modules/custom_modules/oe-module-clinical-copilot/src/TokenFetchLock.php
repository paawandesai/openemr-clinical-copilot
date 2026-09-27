<?php

/**
 * Serializes the token broker's exchanges for one clinician.
 *
 * OpenEMR keeps one oauth_trusted_user row per (client, user). Each
 * authorize writes the code it issues into that row, and the token endpoint
 * restores the OAuth session by looking the code up there
 * (AuthorizationController::processAuthorizeFlowForLaunch() and
 * ::oauthAuthorizeToken()). A second exchange for the same clinician that
 * reaches authorize before the first has redeemed its code overwrites it,
 * and the first redemption is refused: "CSRF check failed", 400, and the
 * route answers token_unavailable. Opening a chart fires several of these at
 * once (DECISIONS.md D64 (token fetch race)).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

interface TokenFetchLock
{
    /** Waits for the named lock; false if it was not obtained in time. */
    public function acquire(string $name): bool;

    public function release(string $name): void;
}
