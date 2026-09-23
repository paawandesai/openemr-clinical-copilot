<?php

/**
 * Files an uploaded PDF in the open patient's chart, once.
 *
 * WHY THE MODULE STORES IT. The agent keeps an upload's bytes in memory
 * only while it extracts (agent/app/workers/document_store.py); the preview
 * route reads the chart's own documents (D30), and D1 says documents are
 * stored in OpenEMR. So after the agent accepts an upload, the module files
 * the same bytes through `\Document::createDocument()` (encrypted at rest,
 * audited) using the write-back's DocumentStore, and the citation chip's
 * document id — the SHA3-512 digest — resolves to that row by
 * `documents.hash`.
 *
 * IDEMPOTENCY (invariant 6). The key is D1's document-level key, OpenEMR's
 * own SHA3-512 of the file, checked before insert: the same bytes on the same
 * chart are the document already there, in any category, however they were
 * uploaded. OpenEMR puts no unique constraint on `documents.hash`, so two
 * identical uploads racing would both see "absent" and both insert; a named
 * database lock per digest serialises the check and the insert.
 *
 * THE SAME BYTES ON ANOTHER CHART (D40 item 8) are refused, 409, never
 * rebound. The agent refuses them first; this refuses them again from the
 * chart's own records, because the agent's binding is process memory and a
 * restart forgets it. Only the two categories this module files into are
 * consulted, so a file filed elsewhere by other means is not affected.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentStorage;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\InvalidWriteBackRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\WriteBackFailed;
use Psr\Log\LoggerInterface;

final readonly class ChartDocuments
{
    public const PDF_MIME_TYPE = 'application/pdf';

    /** The agent's MAX_UPLOAD_BYTES, and upload.mjs's MAX_BYTES. */
    public const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

    public function __construct(
        private DocumentStorage $storage,
        private ChartDocumentIndex $index,
        private LoggerInterface $logger,
    ) {
    }

    /**
     * Refuses a file this module already filed on another chart. Checked
     * before the agent is asked, so a refused file costs no extraction.
     *
     * @throws BriefUnavailable DocumentAttachedElsewhere
     */
    public function assertNotFiledElsewhere(int $pid, AgentDocumentId $document): void
    {
        if ($this->index->isFiledOnAnotherPatient($pid, $document->value, $this->moduleCategoryIds())) {
            throw new BriefUnavailable(BriefFailure::DocumentAttachedElsewhere);
        }
    }

    /**
     * The `documents.id` of this file on this chart, storing it first if it
     * is not there.
     *
     * @param string $filename Already reduced to a safe name by the caller.
     * @throws BriefUnavailable DocumentAttachedElsewhere, UploadRejected, DocumentNotStored
     */
    public function attach(int $pid, DocumentKind $kind, AgentDocumentId $document, string $filename, string $pdf, int $ownerId): ChartDocument
    {
        if (!hash_equals($document->value, hash('sha3-512', $pdf))) {
            throw new \LogicException('The document id is not the digest of these bytes');
        }
        $lock = 'ccw2-doc-' . substr($document->value, 0, 40);
        if (!$this->index->lock($lock)) {
            $this->logger->error('Clinical Co-Pilot could not lock an upload for storing', ['document_id' => $document->value]);
            throw new BriefUnavailable(BriefFailure::DocumentNotStored);
        }
        try {
            $this->assertNotFiledElsewhere($pid, $document);
            $existing = $this->index->findForPatient($pid, $document->value);
            if ($existing !== null) {
                return new ChartDocument($existing, false);
            }
            $stored = $this->storage->store(
                $pid,
                $this->storage->resolveCategoryId($kind->categoryPath()),
                $filename,
                self::PDF_MIME_TYPE,
                $pdf,
                $ownerId,
            );
            return new ChartDocument($stored->documentId, true);
        } catch (InvalidWriteBackRequest $e) {
            // The bytes did not sniff as an accepted PDF, or the category is
            // missing on this instance. Logged by field, never by value.
            $this->logger->error('Clinical Co-Pilot refused to store an upload', ['field' => $e->field]);
            throw new BriefUnavailable(BriefFailure::UploadRejected, null, $e);
        } catch (WriteBackFailed $e) {
            $this->logger->error('Clinical Co-Pilot could not store an upload', ['exception' => $e]);
            throw new BriefUnavailable(BriefFailure::DocumentNotStored, null, $e);
        } finally {
            $this->index->unlock($lock);
        }
    }

    /**
     * @return list<int>
     */
    private function moduleCategoryIds(): array
    {
        $ids = [];
        foreach (DocumentKind::cases() as $kind) {
            try {
                $ids[] = $this->storage->resolveCategoryId($kind->categoryPath());
            } catch (InvalidWriteBackRequest) {
                // A category deleted on this instance has nothing filed in it.
            }
        }
        return $ids;
    }
}
