<?php

/**
 * A request to read back a stored source document, after the checks.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use Symfony\Component\HttpFoundation\Request;

/**
 * The guard for the PDF preview, kept apart from the script that serves the
 * bytes so the rules can be read and tested without a web server — the same
 * split `ChartRequest` makes for the brief.
 *
 * The rules, in order, and why each one is here:
 *
 * 1. **GET.** Reading a document changes nothing. There is deliberately no
 *    CSRF token: a token in a query string leaks through referrers and
 *    history, and CSRF protects state changes, which this is not. OpenEMR's
 *    own document retrieval does the same.
 * 2. **A well-formed `document_id`.**
 * 3. **The `patients`/`docs` ACL**, which is what OpenEMR's own viewer
 *    checks (`_rest_routes_standard.inc.php:505`,
 *    `interface/patient_file/document.php`).
 * 4. **The document belongs to the patient whose chart is open.** OpenEMR
 *    keeps one active patient per session, and this is the check that stops
 *    a guessed or stale id reading another patient's paperwork. It is the
 *    same rule `ChartRequest` applies to the brief.
 *
 * What it deliberately does *not* require is that the document was written
 * by this module. A clinician holding `patients`/`docs` can already open any
 * of that patient's documents in OpenEMR's own viewer, so narrowing here
 * would protect nothing while breaking the preview for a document uploaded
 * any other way.
 */
final readonly class DocumentReadRequest
{
    /** @param int<1, max> $documentId */
    private function __construct(public int $documentId, public int $pid)
    {
    }

    /**
     * @param mixed $sessionPid The session's active patient, as stored.
     * @param \Closure(): bool $mayReadDocuments The patients/docs ACL check.
     * @throws DocumentUnavailable
     */
    public static function fromRequest(Request $request, mixed $sessionPid, \Closure $mayReadDocuments): self
    {
        if (!$request->isMethod('GET')) {
            throw new DocumentUnavailable(DocumentFailure::InvalidRequest);
        }
        $documentId = filter_var(
            $request->query->getString('document_id'),
            FILTER_VALIDATE_INT,
            ['options' => ['min_range' => 1]]
        );
        if ($documentId === false) {
            throw new DocumentUnavailable(DocumentFailure::InvalidRequest);
        }
        if (!$mayReadDocuments()) {
            throw new DocumentUnavailable(DocumentFailure::Forbidden);
        }
        $pid = filter_var($sessionPid, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
        if ($pid === false) {
            throw new DocumentUnavailable(DocumentFailure::NoOpenChart);
        }
        return new self($documentId, $pid);
    }
}
