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

use OpenEMR\Modules\ClinicalCopilot\W2\AgentDocumentId;
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
 * 2. **A well-formed `document_id`**: OpenEMR's integer `documents.id`, or
 *    the Week 2 card's form of it — the agent's document id, which is the
 *    file's SHA3-512 digest (W2\AgentDocumentId) and so the same string
 *    OpenEMR stored in `documents.hash`. A citation chip carries the digest;
 *    SourceDocumentReader resolves it on the open chart only, so it can
 *    reach nothing an integer id could not.
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
    /**
     * Exactly one of `$documentId` and `$sha3512` is set.
     *
     * @param ?int<1, max> $documentId
     * @param ?string $sha3512 Lowercase hex, 128 characters.
     */
    private function __construct(public ?int $documentId, public int $pid, public ?string $sha3512 = null)
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
        $raw = $request->query->getString('document_id');
        $sha3512 = preg_match(AgentDocumentId::PATTERN, $raw) === 1 ? $raw : null;
        $documentId = $sha3512 === null
            ? filter_var($raw, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]])
            : null;
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
        return new self($documentId, $pid, $sha3512);
    }
}
