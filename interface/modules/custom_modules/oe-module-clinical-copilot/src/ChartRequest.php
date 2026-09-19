<?php

/**
 * A request from the chart card that has passed the checks every co-pilot
 * endpoint applies, in this order: POST, CSRF token, a well-formed pid, the
 * medical-record ACL, and that the pid is still the patient open in the
 * session. brief.php and conversation.php both obtain one before doing
 * anything else, so neither can apply a weaker set.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use Symfony\Component\HttpFoundation\Request;

final readonly class ChartRequest
{
    /**
     * @param int<1, max> $pid The patient the card was rendered for, and the one open in the session.
     */
    private function __construct(public int $pid)
    {
    }

    /**
     * @param mixed $sessionPid The session's active patient, as stored.
     * @param \Closure(string): bool $csrfValid CsrfUtils::verifyCsrfToken() for this session.
     * @param \Closure(): bool $mayReadRecord The Bootstrap::ACL check for the logged-in user.
     * @throws BriefUnavailable
     */
    public static function fromRequest(Request $request, mixed $sessionPid, \Closure $csrfValid, \Closure $mayReadRecord): self
    {
        if (!$request->isMethod('POST')) {
            throw new BriefUnavailable(BriefFailure::InvalidRequest);
        }
        if (!$csrfValid($request->request->getString('csrf_token_form'))) {
            throw new BriefUnavailable(BriefFailure::InvalidRequest);
        }
        $requestedPid = filter_var($request->request->getString('pid'), FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
        if ($requestedPid === false) {
            throw new BriefUnavailable(BriefFailure::InvalidRequest);
        }
        if (!$mayReadRecord()) {
            throw new BriefUnavailable(BriefFailure::Forbidden);
        }
        // The card was rendered for one patient; the session may since have
        // moved on to another (OpenEMR keeps one active patient per session).
        // Never answer for a patient other than the one the card shows.
        if (filter_var($sessionPid, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]) !== $requestedPid) {
            throw new BriefUnavailable(BriefFailure::PatientContextChanged);
        }
        return new self($requestedPid);
    }
}
