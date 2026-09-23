<?php

/**
 * Reads a stored source document back for the PDF preview.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use Document;
use OpenEMR\Common\Database\QueryUtils;
use Psr\Log\LoggerInterface;
use Throwable;

/**
 * D1 named `GET /fhir/Binary/:id` as the read-back and the source of the PDF
 * preview. It does not work for a clinician: that route runs
 * `RestConfig::request_authorization_check($request, "admin", "users")`
 * (`_rest_routes_fhir_r4_us_core_3_1_0.inc.php:285`), and a physician has no
 * `admin` ACL, so the preview 403s for exactly the people it is for. The
 * write-back spike saw it work only because it ran as `admin`.
 *
 * So the preview reads through this instead, inside the clinician's own
 * OpenEMR session, with the ACL OpenEMR's own document viewer uses.
 * Decryption is `\Document::get_data()` — the same call the core viewer
 * makes — so a document stored encrypted at rest comes back as plaintext
 * here and nowhere else.
 */
final readonly class SourceDocumentReader
{
    /**
     * The only type served inline. A stored document can have any mime type
     * the upload whitelist allows, and streaming, say, `text/plain` or an
     * SVG inline from OpenEMR's own origin would be a scripting vector on
     * a page that is trusted. The preview wants PDFs; everything else is
     * refused rather than served with a guessed disposition.
     */
    public const PREVIEWABLE_MIME_TYPE = 'application/pdf';

    /** @var \Closure(string, list<int|string|null>): mixed */
    private \Closure $querySingleRow;

    /**
     * @param ?\Closure(string, list<int|string|null>): mixed $querySingleRow
     *     QueryUtils::querySingleRow() outside tests.
     */
    public function __construct(private LoggerInterface $logger, ?\Closure $querySingleRow = null)
    {
        $this->querySingleRow = $querySingleRow ?? QueryUtils::querySingleRow(...);
    }

    /**
     * The one query both forms of `document_id` run. Only the key column
     * differs — `id` for OpenEMR's integer, `hash` for a Week 2 citation's
     * SHA3-512 — so "belongs to the open chart, not deleted" is the same
     * clause, bound to the same session pid, whichever form was asked for.
     * A digest stored twice on one chart (OpenEMR does not deduplicate) is
     * the same bytes, so the oldest row serves; an integer matches one row.
     *
     * @return array{string, list<int|string|null>}
     */
    public static function rowQuery(DocumentReadRequest $request): array
    {
        [$column, $key] = $request->sha3512 !== null
            ? ['hash', $request->sha3512]
            : ['id', $request->documentId];
        return [
            'SELECT `id`, `name`, `mimetype` FROM `documents`'
            . ' WHERE `' . $column . '` = ? AND `foreign_id` = ? AND `deleted` = 0'
            . ' ORDER BY `id` ASC LIMIT 1',
            [$key, $request->pid],
        ];
    }

    /**
     * @throws DocumentUnavailable
     */
    public function read(DocumentReadRequest $request): SourceDocumentBytes
    {
        // By id, or by the digest a Week 2 citation carries: one query,
        // scoped to the open chart either way (rowQuery()).
        [$sql, $binds] = self::rowQuery($request);
        $row = ($this->querySingleRow)($sql, $binds);
        if (!is_array($row) || !isset($row['id'])) {
            // One answer for "no such id", "another patient's document" and
            // "deleted": see DocumentFailure.
            throw new DocumentUnavailable(DocumentFailure::NotFound);
        }
        if (Row::string($row, 'mimetype') !== self::PREVIEWABLE_MIME_TYPE) {
            throw new DocumentUnavailable(DocumentFailure::NotPreviewable);
        }

        $documentId = Row::int($row, 'id');
        $document = new Document($documentId);
        if ($document->is_deleted() || $document->has_expired()) {
            throw new DocumentUnavailable(DocumentFailure::NotFound);
        }

        try {
            $bytes = $document->get_data();
        } catch (Throwable $exception) {
            // Decryption failures name key material and file paths.
            $this->logger->error('Clinical Co-Pilot could not read a source document', [
                'document_id' => $documentId,
                'exception' => $exception,
            ]);
            throw new DocumentUnavailable(DocumentFailure::Unreadable);
        }
        if (!is_string($bytes) || $bytes === '') {
            $this->logger->error('Clinical Co-Pilot read an empty source document', [
                'document_id' => $documentId,
            ]);
            throw new DocumentUnavailable(DocumentFailure::Unreadable);
        }

        return new SourceDocumentBytes(
            $documentId,
            $bytes,
            self::PREVIEWABLE_MIME_TYPE,
            self::safeFilename(Row::string($row, 'name')),
        );
    }

    /**
     * A filename safe to put in a `Content-Disposition` header: ASCII, no
     * quotes, no control characters, no path separators. The stored name
     * came from an upload and is not trusted to be any of those.
     */
    public static function safeFilename(string $stored): string
    {
        $base = basename(str_replace('\\', '/', $stored));
        $cleaned = preg_replace('/[^A-Za-z0-9._-]/', '_', $base) ?? '';
        $cleaned = trim($cleaned, '.');
        if ($cleaned === '') {
            return 'document.pdf';
        }
        return strlen($cleaned) > 100 ? substr($cleaned, 0, 100) : $cleaned;
    }
}
