<?php

/**
 * A request from a chart card that has passed the checks every co-pilot
 * endpoint applies, in this order: the method, CSRF token, a well-formed pid,
 * the ACL, and that the pid is still the patient open in the session.
 * brief.php, conversation.php, and the Week 2 upload.php, upload-status.php
 * and ask.php all obtain one before doing anything else, so none can apply a
 * weaker set.
 *
 * The method is POST except for the extraction-status poll, a GET the
 * browser repeats (docs/w2/ui-integration.md §2.3). A GET reads the same
 * fields from the query string instead of the form body and is checked in
 * the same order; nothing is skipped for it. Which ACL applies is the
 * caller's: the brief reads the medical record, the Week 2 card documents.
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
     * @param \Closure(): bool $mayReadRecord The ACL check for the logged-in user (Bootstrap::ACL, or Bootstrap::DOCUMENTS_ACL).
     * @param 'POST'|'GET' $method
     * @throws BriefUnavailable
     */
    public static function fromRequest(
        Request $request,
        mixed $sessionPid,
        \Closure $csrfValid,
        \Closure $mayReadRecord,
        string $method = 'POST',
    ): self {
        if (!$request->isMethod($method)) {
            throw new BriefUnavailable(BriefFailure::InvalidRequest);
        }
        $fields = $method === 'GET' ? $request->query : $request->request;
        if (!$csrfValid($fields->getString('csrf_token_form'))) {
            throw new BriefUnavailable(BriefFailure::InvalidRequest);
        }
        $requestedPid = filter_var($fields->getString('pid'), FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
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
