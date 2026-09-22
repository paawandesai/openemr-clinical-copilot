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

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Database\SqlQueryException;
use Psr\Log\LoggerInterface;

/**
 * Document and lab result are written together or not at all, so a partial
 * write cannot leave a result pointing at a document that was never
 * stored.
 *
 * Idempotency has two layers, and only the second one is load-bearing. The
 * ledger lookup before the transaction is an optimisation and the source
 * of the ids a replay answers with; two concurrent identical requests can
 * both miss it. The ledger's UNIQUE key is what they then collide on, and
 * the loser re-reads the winner's ids rather than failing — so a retry is
 * safe however it races (CLAUDE.md invariant 6).
 *
 * Known limit, deliberately not solved here: `\Document::createDocument()`
 * writes the encrypted file to disk, and a filesystem write is not part of
 * a database transaction. A rollback after that point removes the
 * `documents` row and leaves the ciphertext behind, unreferenced by
 * anything. That is the safe direction to fail — an orphaned file rather
 * than a row pointing at nothing — but it does mean a failed write can
 * leave bytes on disk.
 */
final readonly class LabWriteBackService
{
    public function __construct(
        private PatientDirectory $patients,
        private DocumentStore $documents,
        private ProcedureChainWriter $chain,
        private WriteBackLedger $ledger,
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
        $result = $request->onlyResult();

        $recorded = $this->ledger->findByIdempotencyKey($result->idempotencyKey);
        if ($recorded !== null) {
            $this->logReplay($request, $recorded);
            return LabWriteBackOutcome::replayed($request, $recorded);
        }

        try {
            /** @var LabWriteBackOutcome $outcome */
            $outcome = QueryUtils::inTransaction(
                fn(): LabWriteBackOutcome => $this->writeOnce($request, $result, $pid, $userId)
            );
        } catch (SqlQueryException $exception) {
            $winner = $this->ledger->findByIdempotencyKey($result->idempotencyKey);
            if ($winner === null) {
                // Not the UNIQUE key, then — a real database failure. The
                // message may carry SQL, so it is logged and not returned.
                $this->logger->error('Clinical Co-Pilot lab write-back failed', [
                    'correlation_id' => $request->correlationId,
                    'extraction_id' => $request->extractionId,
                    'exception' => $exception,
                ]);
                throw new WriteBackFailed('the write could not be completed');
            }
            $this->logger->info('Clinical Co-Pilot lab write-back lost an idempotency race', [
                'correlation_id' => $request->correlationId,
                'extraction_id' => $request->extractionId,
                'procedure_result_id' => $winner->procedureResultId,
            ]);
            return LabWriteBackOutcome::replayed($request, $winner);
        }

        $this->logger->info('Clinical Co-Pilot wrote a lab result back to the chart', [
            'correlation_id' => $request->correlationId,
            'extraction_id' => $request->extractionId,
            'document_id' => $outcome->documentId,
            'document_outcome' => $outcome->documentOutcome->value,
            'procedure_order_id' => $outcome->ids->procedureOrderId,
            'procedure_report_id' => $outcome->ids->procedureReportId,
            'procedure_result_id' => $outcome->ids->procedureResultId,
        ]);
        return $outcome;
    }

    /**
     * @throws InvalidWriteBackRequest|WriteBackFailed|SqlQueryException
     */
    private function writeOnce(
        LabWriteBackRequest $request,
        LabWriteBackResult $result,
        int $pid,
        int $userId
    ): LabWriteBackOutcome {
        $categoryId = $this->documents->resolveCategoryId($request->document->categoryPath);
        [$documentId, $documentOutcome] = $this->resolveDocument($request, $pid, $categoryId, $userId);
        $ids = $this->chain->write($result, $pid, $userId, $documentId);
        $this->ledger->record($request, $result, $documentId, $ids);

        return new LabWriteBackOutcome(
            $request->patientUuid,
            $documentId,
            $documentOutcome,
            $result->testCode,
            $result->idempotencyKey,
            $result->extractionId,
            WriteOutcome::CREATED,
            $ids,
            $request->correlationId,
        );
    }

    /**
     * @return array{int, WriteOutcome}
     * @throws InvalidWriteBackRequest|WriteBackFailed
     */
    private function resolveDocument(
        LabWriteBackRequest $request,
        int $pid,
        int $categoryId,
        int $userId
    ): array {
        $document = $request->document;

        if ($document->existingDocumentId !== null) {
            return [
                $this->documents->requireExisting($document->existingDocumentId, $pid, $document->sha3512),
                WriteOutcome::DUPLICATE_IGNORED,
            ];
        }

        $alreadyStored = $this->documents->findByHash($pid, $categoryId, $document->sha3512);
        if ($alreadyStored !== null) {
            return [$alreadyStored, WriteOutcome::DUPLICATE_IGNORED];
        }

        return [
            $this->documents->store(
                $pid,
                $categoryId,
                $document->filename,
                $document->mimeType,
                (string) $document->content,
                $userId
            ),
            WriteOutcome::CREATED,
        ];
    }

    private function logReplay(LabWriteBackRequest $request, LedgerEntry $recorded): void
    {
        $this->logger->info('Clinical Co-Pilot lab write-back replayed an existing write', [
            'correlation_id' => $request->correlationId,
            'extraction_id' => $recorded->extractionId,
            'document_id' => $recorded->documentId,
            'procedure_result_id' => $recorded->procedureResultId,
        ]);
    }
}
