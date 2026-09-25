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
 * chart's own records, so the rule holds whatever the agent remembers.
 * Only the two categories this module files into are consulted, so a file
 * filed elsewhere by other means is not affected. **A quarantined copy does
 * not count (D60, amending D40 item 8):** it was ruled not that chart's
 * patient's, so its bytes may be attached to the chart whose identity check
 * they pass, and that chart's extraction decides from there.
 *
 * QUARANTINE (D60). The upload is filed before its extraction finishes, so
 * a document whose printed name or date of birth contradicts this chart is
 * first filed like any other. When the agent reports that mismatch,
 * `quarantine()` moves it to its own clearly named category and notes why on
 * the document. It is never deleted: it is the record of what was uploaded
 * to this chart, by whom, and why nothing from it was used. The move is
 * keyed on the digest like the attach (invariant 6): a document already in
 * quarantine is left alone, so repeating the call changes nothing.
 *
 * RECONCILIATION (D60). The status poll can miss the moment an extraction
 * finishes: the page was closed first. `reconcile()` runs when the card
 * loads: it asks the agent about this chart's newest filed, unquarantined
 * copies, and quarantines each one the agent reports as a mismatch.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

use OpenEMR\Common\Database\SqlQueryException;
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

    /**
     * Where a wrong-patient upload is moved (D60): the category
     * `sql/install.sql` creates, "Quarantine - Patient Mismatch", as a path
     * DocumentService resolves (underscores for spaces).
     */
    public const QUARANTINE_CATEGORY_PATH = '/Categories/Quarantine_-_Patient_Mismatch';

    /**
     * How many of a chart's newest unquarantined copies `reconcile()` asks
     * the agent about per card load: one status call each.
     */
    public const RECONCILE_LIMIT = 10;

    /** At most 255 characters: `notes.note` is a VARCHAR(255). */
    public const QUARANTINE_NOTE = 'Quarantined by Clinical Co-Pilot: the name or date of birth printed on this'
        . ' document does not match this chart. Nothing from it was used or written to the chart.'
        . ' Kept, not deleted. Reference: ';

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
        $lock = self::lockName($document);
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
     * Moves this chart's copy of a document into the quarantine category
     * and notes why on it, once. Never throws: the card reports the outcome,
     * and a failure leaves the document where it was filed.
     */
    public function quarantine(int $pid, AgentDocumentId $document, string $reference, int $ownerId): QuarantineResult
    {
        try {
            $quarantine = $this->storage->resolveCategoryId(self::QUARANTINE_CATEGORY_PATH);
        } catch (InvalidWriteBackRequest) {
            $this->logger->error('Clinical Co-Pilot has no quarantine category; re-run the module install SQL', ['document_id' => $document->value]);
            return new QuarantineResult(QuarantineOutcome::Failed);
        }
        $lock = self::lockName($document);
        if (!$this->index->lock($lock)) {
            $this->logger->error('Clinical Co-Pilot could not lock an upload for quarantine', ['document_id' => $document->value]);
            return new QuarantineResult(QuarantineOutcome::Failed);
        }
        try {
            $documentId = $this->index->findForPatient($pid, $document->value);
            if ($documentId === null) {
                return new QuarantineResult(QuarantineOutcome::NotOnChart);
            }
            if ($this->index->isInCategory($documentId, $quarantine)) {
                return new QuarantineResult(QuarantineOutcome::Quarantined, $documentId);
            }
            $this->index->moveToCategory($documentId, $quarantine);
            $this->index->addNote($documentId, $ownerId, self::QUARANTINE_NOTE . $reference);
            return new QuarantineResult(QuarantineOutcome::Quarantined, $documentId, true);
        } catch (SqlQueryException $e) {
            // The database refused the move or the note. Anything else is a
            // bug and goes to the global handler.
            $this->logger->error('Clinical Co-Pilot could not quarantine an upload', ['document_id' => $document->value, 'exception' => $e]);
            return new QuarantineResult(QuarantineOutcome::Failed);
        } finally {
            $this->index->unlock($lock);
        }
    }

    /**
     * Quarantines every copy on this chart whose extraction the agent reports
     * as an identity mismatch and that is not yet quarantined (D60). Never
     * throws. With no candidate copies, `$stateOf` is never called, so a
     * chart with nothing to check costs no token and no agent call.
     *
     * @param \Closure(): (\Closure(AgentDocumentId): ?ExtractionState) $agent
     *     Builds the agent lookup once, only when there is something to look
     *     up. The lookup answers null for a document the agent does not know
     *     under this chart, or could not be asked about.
     */
    public function reconcile(int $pid, \Closure $agent, string $reference, int $ownerId): ReconcileResult
    {
        try {
            $quarantine = $this->storage->resolveCategoryId(self::QUARANTINE_CATEGORY_PATH);
            $candidates = $this->index->filedNotIn($pid, $this->moduleCategoryIds(), $quarantine, self::RECONCILE_LIMIT);
        } catch (InvalidWriteBackRequest | SqlQueryException) {
            $this->logger->error('Clinical Co-Pilot could not list documents to reconcile');
            return new ReconcileResult();
        }
        if ($candidates === []) {
            return new ReconcileResult();
        }
        $stateOf = $agent();
        $checked = 0;
        $moved = [];
        $failed = 0;
        foreach ($candidates as $candidate) {
            try {
                $document = new AgentDocumentId($candidate['hash']);
            } catch (\DomainException) {
                continue; // not a SHA3-512 digest: not a file this module filed
            }
            $checked++;
            $state = $stateOf($document);
            if ($state === null || !$state->isIdentityMismatch()) {
                continue;
            }
            $result = $this->quarantine($pid, $document, $reference, $ownerId);
            if ($result->outcome === QuarantineOutcome::Failed) {
                $failed++;
            } elseif ($result->moved && $result->documentId !== null) {
                $moved[] = $result->documentId;
            }
        }
        return new ReconcileResult($checked, $moved, $failed);
    }

    private static function lockName(AgentDocumentId $document): string
    {
        return 'ccw2-doc-' . substr($document->value, 0, 40);
    }

    /**
     * @return list<int>
     */
    private function moduleCategoryIds(): array
    {
        $ids = [];
        // Not the quarantine category (D60): a quarantined copy was ruled
        // another patient's and binds its bytes to no chart.
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
