<?php

/**
 * The one-transaction lab write-back D1 settled on.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use OpenEMR\Common\Database\SqlQueryException;
use Psr\Log\LoggerInterface;
use Throwable;

/**
 * Document and lab results are written together or not at all, so a partial
 * write cannot leave results pointing at a document that was never stored.
 *
 * ### Idempotency is per result, and the constraint is what enforces it
 *
 * Each result carries its own D1 key. The ledger is read before the
 * transaction to decide what is new, but that read is an optimisation and a
 * source of ids — two concurrent identical requests can both miss it. The
 * ledger's UNIQUE key is what they then collide on, and the loser rolls
 * back, re-reads, and answers with the winner's ids. So a retry is safe
 * however it races (CLAUDE.md invariant 6), and a request that repeats some
 * results and adds others writes only the new ones.
 *
 * ### The orphaned-file problem, and what is done about it
 *
 * `\Document::createDocument()` encrypts the bytes and writes them to disk
 * before the `documents` row is committed, and a filesystem write is not
 * part of a database transaction. A rollback after that point removes the
 * row and leaves ciphertext behind that nothing references. The rollback is
 * still the right direction to fail — an unreferenced file beats a row
 * pointing at nothing — but the file is this call's litter, so the failure
 * path deletes it. Only a file *this call* wrote: a document that was
 * already stored belongs to whoever stored it.
 */
final readonly class LabWriteBackService
{
    public function __construct(
        private PatientLookup $patients,
        private DocumentStorage $documents,
        private ProcedureChain $chain,
        private Ledger $ledger,
        private Transaction $transaction,
        private WriteBackAudit $audit,
        private LoggerInterface $logger,
    ) {
    }

    /**
     * @param int $userId `users.id` of the clinician whose token this is.
     * @throws InvalidWriteBackRequest|WriteBackFailed
     */
    public function write(LabWriteBackRequest $request, int $userId): LabWriteBackOutcome
    {
        $pid = $this->patients->requirePid($request->patientUuid);

        $recorded = $this->readLedger($request);
        if ($this->allRecorded($request, $recorded)) {
            $this->log($request, 'replayed an existing write', $recorded);
            return $this->replay($request, $recorded);
        }

        try {
            $outcome = $this->transaction->run(
                fn(): LabWriteBackOutcome => $this->writeOnce($request, $recorded, $pid, $userId)
            );
        } catch (SqlQueryException $exception) {
            return $this->afterRollback($request, $pid, $exception);
        } catch (Throwable $exception) {
            $this->audit->writeAbandoned($pid, 'the transaction was rolled back');
            throw $exception;
        }

        // After the commit, never inside it: OpenEMR's audit inserts do not
        // roll back with the work they describe, so an entry written inside
        // the transaction can outlive rows that were undone.
        $this->audit->orderWritten(
            $pid,
            $outcome->procedureOrderId,
            array_values(array_map(
                static fn(ResultOutcome $r): int => $r->ids->procedureResultId,
                array_filter(
                    $outcome->results,
                    static fn(ResultOutcome $r): bool => $r->outcome === WriteOutcome::CREATED
                )
            ))
        );

        $this->logger->info('Clinical Co-Pilot wrote lab results back to the chart', [
            'correlation_id' => $request->correlationId,
            'extraction_id' => $request->extractionId,
            'document_id' => $outcome->documentId,
            'document_outcome' => $outcome->documentOutcome->value,
            'procedure_order_id' => $outcome->procedureOrderId,
            'results_created' => $outcome->createdCount(),
            'results_total' => count($outcome->results),
        ]);
        return $outcome;
    }

    /**
     * The body of the transaction.
     *
     * The document is stored inside it on purpose: storing first and
     * committing later would leave a document in the chart whose results
     * never arrived, which is the failure D1's "one transaction" exists to
     * prevent. The cost is the orphaned file, which the caller cleans up.
     *
     * @param array<string, LedgerEntry|null> $recorded keyed by idempotency key
     * @throws InvalidWriteBackRequest|WriteBackFailed|SqlQueryException
     */
    private function writeOnce(
        LabWriteBackRequest $request,
        array $recorded,
        int $pid,
        int $userId
    ): LabWriteBackOutcome {
        $categoryId = $this->documents->resolveCategoryId($request->document->categoryPath);
        $stored = $this->resolveDocument($request, $pid, $categoryId, $userId);

        try {
            $pending = [];
            foreach ($request->results as $result) {
                if (($recorded[$result->idempotencyKey] ?? null) === null) {
                    $pending[] = $result;
                }
            }
            $written = $this->chain->write($pending, $pid, $userId, $stored->documentId);

            $byKey = [];
            foreach ($pending as $index => $result) {
                $this->ledger->record($request, $result, $stored->documentId, $written[$index]);
                $byKey[$result->idempotencyKey] = ResultOutcome::created($result, $written[$index]);
            }

            // array_map over a non-empty list, so the response's result list
            // is non-empty by construction rather than by assertion.
            $outcomes = array_map(
                static fn(LabWriteBackResult $result): ResultOutcome =>
                    ($recorded[$result->idempotencyKey] ?? null) === null
                        ? $byKey[$result->idempotencyKey]
                        : ResultOutcome::alreadyWritten($result, $recorded[$result->idempotencyKey]),
                $request->results
            );

            return new LabWriteBackOutcome(
                $request->patientUuid,
                $stored->documentId,
                $stored->outcome,
                $written[0]->procedureOrderId,
                $outcomes,
                $request->correlationId,
            );
        } catch (Throwable $exception) {
            // The transaction is about to roll back and take the
            // `documents` row with it. Remove the bytes it left on disk.
            $this->documents->discardStoredFile($stored);
            throw $exception;
        }
    }

    /**
     * A failed transaction is either a lost idempotency race or a real
     * fault, and the ledger says which: after the rollback, every key this
     * request carries is present exactly when someone else wrote them.
     *
     * @throws WriteBackFailed
     */
    private function afterRollback(
        LabWriteBackRequest $request,
        int $pid,
        SqlQueryException $exception
    ): LabWriteBackOutcome {
        $recorded = $this->readLedger($request);
        if (!$this->allRecorded($request, $recorded)) {
            $this->audit->writeAbandoned($pid, 'the transaction was rolled back');
            // Not the UNIQUE key, then — a real database failure. The
            // message may carry SQL, so it is logged and not returned.
            $this->logger->error('Clinical Co-Pilot lab write-back failed', [
                'correlation_id' => $request->correlationId,
                'extraction_id' => $request->extractionId,
                'results_total' => count($request->results),
                'exception' => $exception,
            ]);
            throw new WriteBackFailed('the write could not be completed');
        }
        $this->audit->writeAbandoned($pid, 'another request wrote the same results first');
        $this->log($request, 'lost an idempotency race', $recorded);
        return $this->replay($request, $recorded);
    }

    /**
     * @return array<string, LedgerEntry|null> keyed by idempotency key
     */
    private function readLedger(LabWriteBackRequest $request): array
    {
        $recorded = [];
        foreach ($request->results as $result) {
            $recorded[$result->idempotencyKey] = $this->ledger->findByIdempotencyKey($result->idempotencyKey);
        }
        return $recorded;
    }

    /** @param array<string, LedgerEntry|null> $recorded */
    private function allRecorded(LabWriteBackRequest $request, array $recorded): bool
    {
        foreach ($request->results as $result) {
            if (($recorded[$result->idempotencyKey] ?? null) === null) {
                return false;
            }
        }
        return true;
    }

    /**
     * Every result was already written: return what was recorded, having
     * written nothing.
     *
     * @param array<string, LedgerEntry|null> $recorded
     */
    private function replay(LabWriteBackRequest $request, array $recorded): LabWriteBackOutcome
    {
        // allRecorded() has already established every entry is present.
        $outcomes = array_map(
            static fn(LabWriteBackResult $result): ResultOutcome => ResultOutcome::alreadyWritten(
                $result,
                $recorded[$result->idempotencyKey] ?? throw new WriteBackFailed('ledger row vanished')
            ),
            $request->results
        );
        $first = $recorded[$request->results[0]->idempotencyKey];
        assert($first instanceof LedgerEntry);

        return new LabWriteBackOutcome(
            $request->patientUuid,
            $first->documentId,
            WriteOutcome::DUPLICATE_IGNORED,
            $first->procedureOrderId,
            $outcomes,
            $request->correlationId,
        );
    }

    /**
     * @throws InvalidWriteBackRequest|WriteBackFailed
     */
    private function resolveDocument(
        LabWriteBackRequest $request,
        int $pid,
        int $categoryId,
        int $userId
    ): StoredDocument {
        $document = $request->document;

        if ($document->existingDocumentId !== null) {
            return StoredDocument::reused(
                $this->documents->requireExisting($document->existingDocumentId, $pid, $document->sha3512)
            );
        }

        $alreadyStored = $this->documents->findByHash($pid, $categoryId, $document->sha3512);
        if ($alreadyStored !== null) {
            return StoredDocument::reused($alreadyStored);
        }

        return $this->documents->store(
            $pid,
            $categoryId,
            $document->filename,
            $document->mimeType,
            (string) $document->content,
            $userId
        );
    }

    /** @param array<string, LedgerEntry|null> $recorded */
    private function log(LabWriteBackRequest $request, string $what, array $recorded): void
    {
        $this->logger->info('Clinical Co-Pilot lab write-back ' . $what, [
            'correlation_id' => $request->correlationId,
            'extraction_id' => $request->extractionId,
            'results_total' => count($request->results),
            'results_already_written' => count(array_filter($recorded)),
        ]);
    }
}
