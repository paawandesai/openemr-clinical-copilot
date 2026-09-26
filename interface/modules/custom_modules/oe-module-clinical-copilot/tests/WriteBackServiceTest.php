<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests;

use OpenEMR\Common\Database\SqlQueryException;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\FixedPatientLookup;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\InMemoryChartResults;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\InMemoryLedger;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\RecordingAudit;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\RecordingDocumentStorage;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\RecordingTransaction;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\ScriptedProcedureChain;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\ChartResult;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\IdempotencyKeys;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackOutcome;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackService;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LedgerEntry;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\ResultOutcome;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\WriteBackFailed;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\WriteOutcome;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use RuntimeException;

/**
 * The orchestration, with the four database collaborators faked.
 *
 * These are the two paths that cannot be checked by sending a request: a
 * transaction that fails half way, and two identical requests arriving at
 * once. Both are verified against the live stack as well — see the session
 * report — but only here can they be run in fifty milliseconds and only
 * here can the *cleanup* be observed, since after a real rollback there is
 * nothing left to look at except the absence of rows.
 */
final class WriteBackServiceTest extends TestCase
{
    private const EXTRACTOR = 'writeback-checkpoint-probe/1';

    public function testAPanelIsWrittenAsOneOrderWithOneResultRowEach(): void
    {
        $chain = new ScriptedProcedureChain();
        $ledger = new InMemoryLedger();
        $transaction = new RecordingTransaction();
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(), $transaction);

        $outcome = $service->write($this->request(['2345-7', '2823-3', '2951-2']), 7);

        $this->assertSame([3], $chain->batchSizes, 'the panel is one call, not three');
        $this->assertSame(8198, $outcome->procedureOrderId);
        $this->assertCount(3, $outcome->results);
        $this->assertSame(3, $outcome->createdCount());
        $this->assertCount(3, $ledger->rows);
        $this->assertSame(1, $transaction->committed);
        $this->assertSame(0, $transaction->rolledBack);
        $this->assertSame(
            [['pid' => 1, 'order' => 8198, 'results' => [14849, 14850, 14851]]],
            $this->audit->written
        );
        $this->assertSame([], $this->audit->abandoned);
    }

    public function testResultsAlreadyWrittenAreSkippedAndTheRestAreWritten(): void
    {
        $ledger = new InMemoryLedger();
        $alreadyThere = $this->keyFor('2823-3');
        $ledger->rows[$alreadyThere] = new LedgerEntry(
            $alreadyThere,
            $this->extractionIdFor('2823-3'),
            2474,
            8000,
            700,
            14000,
        );
        $chain = new ScriptedProcedureChain();
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(), new RecordingTransaction());

        $outcome = $service->write($this->request(['2345-7', '2823-3', '2951-2']), 7);

        $this->assertSame([2], $chain->batchSizes, 'only the two new results reach the chain');
        $this->assertSame(2, $outcome->createdCount());
        $outcomes = [];
        foreach ($outcome->results as $result) {
            $outcomes[$result->testCode] = $result->outcome;
        }
        $this->assertSame(WriteOutcome::CREATED, $outcomes['2345-7']);
        $this->assertSame(WriteOutcome::DUPLICATE_IGNORED, $outcomes['2823-3']);
        $this->assertSame(WriteOutcome::CREATED, $outcomes['2951-2']);
        // The replayed row answers with the ids of the write that made it,
        // not with ids from this call.
        foreach ($outcome->results as $result) {
            if ($result->testCode === '2823-3') {
                $this->assertSame(14000, $result->ids?->procedureResultId);
            }
        }
    }

    public function testAnIdenticalRequestWritesNothingAndReturnsTheSameIds(): void
    {
        $chain = new ScriptedProcedureChain();
        $ledger = new InMemoryLedger();
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(), new RecordingTransaction());

        $first = $service->write($this->request(['2345-7', '2823-3']), 7);
        $second = $service->write($this->request(['2345-7', '2823-3']), 7);

        $this->assertSame(1, $chain->calls, 'the second request does not reach the chain');
        $this->assertSame(0, $second->createdCount());
        $this->assertSame(WriteOutcome::DUPLICATE_IGNORED, $second->documentOutcome);
        $this->assertSame($first->procedureOrderId, $second->procedureOrderId);
        $this->assertSame($first->documentId, $second->documentId);
        // Same ids, result for result, in the same order; only the outcome
        // differs, which is the whole point of a replay.
        $this->assertSame(self::idsOf($first), self::idsOf($second));
    }

    // -- a transaction that fails half way --------------------------------

    public function testAFailureAfterTheDocumentIsStoredWritesNothingAndLeavesNoOrphanFile(): void
    {
        $storage = new RecordingDocumentStorage();
        $ledger = new InMemoryLedger();
        $transaction = new RecordingTransaction();
        $chain = new ScriptedProcedureChain(new RuntimeException('the chain fell over'));
        $service = $this->service($chain, $ledger, $storage, $transaction);

        try {
            $service->write($this->request(['2345-7', '2823-3']), 7);
            $this->fail('expected the failure to propagate');
        } catch (RuntimeException $exception) {
            $this->assertSame('the chain fell over', $exception->getMessage());
        }

        $this->assertSame(1, $transaction->rolledBack);
        $this->assertSame(0, $transaction->committed);
        $this->assertSame([], $ledger->rows, 'no ledger row survives the rollback');
        $this->assertCount(1, $storage->discarded, 'the ciphertext this call wrote is removed');
        $this->assertSame(
            RecordingDocumentStorage::STORED_PATH,
            $storage->discarded[0]->path,
        );
        // Nothing was committed, so nothing claims to have been.
        $this->assertSame([], $this->audit->written);
        $this->assertSame(
            [['pid' => 1, 'reason' => 'the transaction was rolled back']],
            $this->audit->abandoned
        );
    }

    public function testADatabaseFailureBecomesAWriteBackFailedWithNothingWritten(): void
    {
        $storage = new RecordingDocumentStorage();
        $ledger = new InMemoryLedger();
        $chain = new ScriptedProcedureChain(
            new SqlQueryException('INSERT INTO `procedure_result`', 'Table is full', 1114)
        );
        $service = $this->service($chain, $ledger, $storage, new RecordingTransaction());

        try {
            $service->write($this->request(['2345-7']), 7);
            $this->fail('expected WriteBackFailed');
        } catch (WriteBackFailed $exception) {
            // The driver's message can carry SQL; the caller is told nothing of it.
            $this->assertSame('the write could not be completed', $exception->getMessage());
            $this->assertStringNotContainsString('INSERT', $exception->getMessage());
        }
        $this->assertSame([], $ledger->rows);
        $this->assertCount(1, $storage->discarded);
    }

    public function testADocumentThatWasAlreadyStoredIsNeverDiscarded(): void
    {
        // The rollback must not delete bytes this call did not write.
        $storage = new RecordingDocumentStorage(existingIdForHash: 2474);
        $chain = new ScriptedProcedureChain(new RuntimeException('boom'));
        $service = $this->service($chain, new InMemoryLedger(), $storage, new RecordingTransaction());

        try {
            $service->write($this->request(['2345-7']), 7);
        } catch (RuntimeException) {
            // expected
        }

        $this->assertSame(0, $storage->stored);
        $this->assertCount(1, $storage->discarded);
        $this->assertNull($storage->discarded[0]->path, 'nothing on disk to remove');
    }

    // -- two identical requests at once ------------------------------------

    public function testTheLoserOfAnIdempotencyRaceReturnsTheWinnersIds(): void
    {
        $key = $this->keyFor('2345-7');
        $winner = [
            $key => new LedgerEntry(
                $key,
                $this->extractionIdFor('2345-7'),
                9001,
                9002,
                9003,
                9004,
            ),
        ];
        $storage = new RecordingDocumentStorage();
        $ledger = (new InMemoryLedger())->withRaceWinner($winner);
        $transaction = new RecordingTransaction();
        $chain = new ScriptedProcedureChain();
        $service = $this->service($chain, $ledger, $storage, $transaction);

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertSame(1, $transaction->rolledBack, 'our own transaction is abandoned');
        $this->assertSame(0, $transaction->committed);
        $this->assertSame(WriteOutcome::DUPLICATE_IGNORED, $outcome->results[0]->outcome);
        $this->assertSame(9004, $outcome->results[0]->ids?->procedureResultId);
        $this->assertSame(9002, $outcome->procedureOrderId);
        $this->assertSame(9001, $outcome->documentId);
        $this->assertCount(1, $storage->discarded, 'the loser cleans up its own ciphertext');
        // The loser's audit trail records an attempt that wrote nothing, not
        // an order id that its own rollback removed.
        $this->assertSame([], $this->audit->written);
        $this->assertSame(
            [['pid' => 1, 'reason' => 'another request wrote the same results first']],
            $this->audit->abandoned
        );
    }

    public function testTheChainRanOnceEvenThoughBothRequestsTried(): void
    {
        // The losing request did reach the chain — it had to, to find out it
        // had lost — but the rows it wrote are gone with the transaction, so
        // exactly one chain survives. This asserts the visible consequence:
        // the ids returned are the winner's, not the ones this call made.
        $key = $this->keyFor('2345-7');
        $ledger = (new InMemoryLedger())->withRaceWinner([
            $key => new LedgerEntry($key, $this->extractionIdFor('2345-7'), 1, 2, 3, 4),
        ]);
        $chain = new ScriptedProcedureChain(orderId: 5555);
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(), new RecordingTransaction());

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertNotSame(5555, $outcome->procedureOrderId);
        $this->assertSame(2, $outcome->procedureOrderId);
        $this->assertCount(1, $ledger->rows, 'exactly one chain is recorded');
    }

    // -- the same results in a different file (work order 34) ---------------

    public function testAResultTheChartAlreadyHoldsIsNotWrittenAgainAndNamesTheExistingRow(): void
    {
        // Another file's write, a lab interface, a hand entry: any source.
        // Same test, same day, same value and unit -> already in the chart.
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(5001, '1', 'mmol/L', '2026-09-20 00:00:00'));
        $chain = new ScriptedProcedureChain();
        $ledger = new InMemoryLedger();
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7', '2823-3']), 7);

        $this->assertSame([1], $chain->batchSizes, 'only the row the chart lacks reaches the chain');
        $this->assertSame(['created' => 1, 'duplicate_ignored' => 0, 'already_in_chart' => 1, 'conflict' => 0], $outcome->counts());
        $byCode = self::byCode($outcome);
        $this->assertSame(WriteOutcome::ALREADY_IN_CHART, $byCode['2345-7']->outcome);
        $this->assertSame(5001, $byCode['2345-7']->matchedProcedureResultId);
        $this->assertSame(5001, $byCode['2345-7']->ids?->procedureResultId, 'the ids are the existing row\'s');
        $this->assertSame(WriteOutcome::CREATED, $byCode['2823-3']->outcome);
        $this->assertCount(1, $ledger->rows, 'the ledger records only what was written');
        $this->assertArrayHasKey($this->keyFor('2823-3'), $ledger->rows);
        $this->assertSame([], $this->audit->conflicts);
    }

    public function testAByteVariantOfTheSameReportWritesNothing(): void
    {
        // The first file's rows, as this endpoint wrote them; the second
        // file has a different digest, so the ledger knows none of its keys.
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(5001, '1', 'mmol/L', '2026-09-20 00:00:00'))
            ->with(1, '2823-3', self::chartRow(5002, '1.0', 'mmol/L', '2026-09-20 00:00:00'));
        $chain = new ScriptedProcedureChain();
        $ledger = new InMemoryLedger();
        $transaction = new RecordingTransaction();
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(existingIdForHash: 2474), $transaction, $chart);

        $outcome = $service->write($this->request(['2345-7', '2823-3']), 7);

        $this->assertSame(0, $chain->calls, 'nothing reaches the chain');
        $this->assertSame(0, $outcome->createdCount());
        $this->assertSame(2, $outcome->counts()['already_in_chart']);
        $this->assertSame([], $ledger->rows);
        $this->assertSame(1, $transaction->committed);
        $this->assertSame([], $this->audit->written, 'no order was written, so none is audited');
        // The order named is the one the matched rows already belong to.
        $this->assertSame(4001, $outcome->procedureOrderId);
    }

    public function testADifferentValueIsAConflictWrittenNowhereWithBothValuesReported(): void
    {
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(5001, '2.5', 'mmol/L', '2026-09-20 00:00:00'));
        $chain = new ScriptedProcedureChain();
        $ledger = new InMemoryLedger();
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertSame(0, $chain->calls);
        $this->assertSame(['created' => 0, 'duplicate_ignored' => 0, 'already_in_chart' => 0, 'conflict' => 1], $outcome->counts());
        $conflict = $outcome->results[0];
        $this->assertSame(WriteOutcome::CONFLICT, $conflict->outcome);
        $this->assertNull($conflict->ids, 'a conflict has no row of its own');
        $this->assertSame(5001, $conflict->matchedProcedureResultId);
        $this->assertSame('2.5', $conflict->chartValue);
        $this->assertSame('mmol/L', $conflict->chartUnit);
        $this->assertNull($outcome->procedureOrderId, 'no result has an order');
        $this->assertSame([], $ledger->rows);
        $this->assertSame([['pid' => 1, 'chart_results' => [5001]]], $this->audit->conflicts);
        $this->assertSame([], $this->audit->written);
        $this->assertSame([], $this->audit->abandoned);
    }

    public function testADifferentUnitIsAConflictToo(): void
    {
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(5001, '1', 'mg/dL', '2026-09-20 00:00:00'));
        $service = $this->service(new ScriptedProcedureChain(), new InMemoryLedger(), new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertSame(WriteOutcome::CONFLICT, $outcome->results[0]->outcome);
        $this->assertSame('mg/dL', $outcome->results[0]->chartUnit);
    }

    public function testASameValueRowWinsOverAConflictingOneOnTheSameDay(): void
    {
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(5001, '2.5', 'mmol/L', '2026-09-20 00:00:00'))
            ->with(1, '2345-7', self::chartRow(5002, '1', 'mmol/L', '2026-09-20 00:00:00'));
        $service = $this->service(new ScriptedProcedureChain(), new InMemoryLedger(), new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertSame(WriteOutcome::ALREADY_IN_CHART, $outcome->results[0]->outcome);
        $this->assertSame(5002, $outcome->results[0]->matchedProcedureResultId);
    }

    public function testADifferentCollectionDateIsANewResult(): void
    {
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(5001, '1', 'mmol/L', '2026-09-19 00:00:00'))
            ->with(1, '2345-7', self::chartRow(5002, '1', 'mmol/L', '2026-09-21 00:00:00'));
        $chain = new ScriptedProcedureChain();
        $ledger = new InMemoryLedger();
        $service = $this->service($chain, $ledger, new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertSame([1], $chain->batchSizes);
        $this->assertSame(WriteOutcome::CREATED, $outcome->results[0]->outcome);
        $this->assertCount(1, $ledger->rows);
    }

    public function testADifferentCollectionTimeOnTheSameDayIsANewResultButADateOnlyRowMatches(): void
    {
        // The report prints 07:12. A chart row at 09:30 is another draw; a
        // row stored at midnight recorded no time and is matched on the day.
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(5001, '1', 'mmol/L', '2026-09-20 09:30:00'))
            ->with(1, '2823-3', self::chartRow(5002, '1', 'mmol/L', '2026-09-20 00:00:00'))
            ->with(1, '2951-2', self::chartRow(5003, '1', 'mmol/L', '2026-09-20 07:12:00'));
        $chain = new ScriptedProcedureChain();
        $service = $this->service($chain, new InMemoryLedger(), new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7', '2823-3', '2951-2'], '2026-09-20T07:12:00'), 7);

        $byCode = self::byCode($outcome);
        $this->assertSame(WriteOutcome::CREATED, $byCode['2345-7']->outcome);
        $this->assertSame(WriteOutcome::ALREADY_IN_CHART, $byCode['2823-3']->outcome);
        $this->assertSame(WriteOutcome::ALREADY_IN_CHART, $byCode['2951-2']->outcome);
        $this->assertSame([1], $chain->batchSizes);
    }

    public function testAnotherPatientsResultIsNotAMatch(): void
    {
        $chart = (new InMemoryChartResults())
            ->with(2, '2345-7', self::chartRow(5001, '1', 'mmol/L', '2026-09-20 00:00:00'));
        $chain = new ScriptedProcedureChain();
        $service = $this->service($chain, new InMemoryLedger(), new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertSame(WriteOutcome::CREATED, $outcome->results[0]->outcome);
        $this->assertSame([[1, '2345-7', '2026-09-20', null]], $chart->lookups, 'looked up for this patient only');
    }

    public function testTheLedgerStillAnswersFirstForTheSameFile(): void
    {
        // The ledger's answer for a key it holds is unchanged; the chart is
        // not consulted for that row at all.
        $ledger = new InMemoryLedger();
        $key = $this->keyFor('2345-7');
        $ledger->rows[$key] = new LedgerEntry($key, $this->extractionIdFor('2345-7'), 2474, 8000, 700, 14000);
        $chart = (new InMemoryChartResults())
            ->with(1, '2345-7', self::chartRow(14000, '9', 'mmol/L', '2026-09-20 00:00:00'));
        $service = $this->service(new ScriptedProcedureChain(), $ledger, new RecordingDocumentStorage(), new RecordingTransaction(), $chart);

        $outcome = $service->write($this->request(['2345-7']), 7);

        $this->assertSame(WriteOutcome::DUPLICATE_IGNORED, $outcome->results[0]->outcome);
        $this->assertSame([], $chart->lookups);
    }

    // -- helpers -----------------------------------------------------------

    private RecordingAudit $audit;

    protected function setUp(): void
    {
        $this->audit = new RecordingAudit();
    }

    private function service(
        ScriptedProcedureChain $chain,
        InMemoryLedger $ledger,
        RecordingDocumentStorage $storage,
        RecordingTransaction $transaction,
        ?InMemoryChartResults $chart = null
    ): LabWriteBackService {
        return new LabWriteBackService(
            new FixedPatientLookup(),
            $storage,
            $chain,
            $ledger,
            $chart ?? new InMemoryChartResults(),
            $transaction,
            $this->audit,
            new NullLogger(),
        );
    }

    private static function chartRow(int $resultId, string $result, string $units, string $collected): ChartResult
    {
        return new ChartResult(4001, 4100 + $resultId % 100, $resultId, $result, $units, new \DateTimeImmutable($collected));
    }

    /** @return array<string, ResultOutcome> */
    private static function byCode(LabWriteBackOutcome $outcome): array
    {
        $byCode = [];
        foreach ($outcome->results as $result) {
            $byCode[$result->testCode] = $result;
        }
        return $byCode;
    }

    /**
     * Everything about a response's results except the outcome, which is the
     * only thing a replay is allowed to change.
     *
     * @return list<array{string, string, string, ?int, ?int}>
     */
    private static function idsOf(LabWriteBackOutcome $outcome): array
    {
        return array_map(
            static fn(ResultOutcome $result): array => [
                $result->testCode,
                $result->idempotencyKey,
                $result->extractionId,
                $result->ids?->procedureReportId,
                $result->ids?->procedureResultId,
            ],
            $outcome->results
        );
    }

    /**
     * The bytes the tests send. A request that sends content has to send
     * content whose digest it also sends — the endpoint recomputes it — so
     * the keys below are derived from this, not hard-coded.
     */
    private const SENT_CONTENT = 'x';

    private static function sentDigest(): string
    {
        return hash('sha3-512', self::SENT_CONTENT);
    }

    private function keyFor(string $testCode): string
    {
        return IdempotencyKeys::labResultKey(self::sentDigest(), $testCode, new \DateTimeImmutable('2026-09-20'));
    }

    private function extractionIdFor(string $testCode): string
    {
        return IdempotencyKeys::labResultExtractionId(
            self::sentDigest(),
            $testCode,
            new \DateTimeImmutable('2026-09-20')
        );
    }

    /** @param list<string> $testCodes */
    private function request(array $testCodes, ?string $collectedAt = null): LabWriteBackRequest
    {
        $results = [];
        foreach ($testCodes as $code) {
            $key = $this->keyFor($code);
            $results[] = [
                'test_code' => $code,
                'test_name' => 'Analyte ' . $code,
                'value' => ['kind' => 'numeric', 'value' => 1.0],
                'unit' => 'mmol/L',
                'reference_range' => ['low' => 0.5, 'high' => 2.0, 'text' => '0.5 - 2.0'],
                'collection_date' => '2026-09-20',
                'collected_at' => $collectedAt,
                'abnormal' => 'normal',
                'page' => 1,
                'bbox' => ['page' => 1, 'x0' => 1.0, 'y0' => 2.0, 'x1' => 3.0, 'y1' => 4.0],
                'idempotency_key' => $key,
                'extraction_id' => 'w2x-' . substr(hash('sha3-256', $key), 0, 32),
            ];
        }
        return LabWriteBackRequest::fromArray([
            'patient_uuid' => 'a2c36e54-af42-4c38-b86e-ade616ed5494',
            'document' => [
                'filename' => 'lab.pdf',
                'mime_type' => 'application/pdf',
                'sha3_512' => self::sentDigest(),
                'byte_length' => strlen(self::SENT_CONTENT),
                'content_base64' => base64_encode(self::SENT_CONTENT),
            ],
            'results' => $results,
            'extraction_id' => IdempotencyKeys::documentExtractionId(self::sentDigest(), self::EXTRACTOR),
            'extractor_version' => self::EXTRACTOR,
            'correlation_id' => '11111111-2222-4333-8444-555555555555',
        ]);
    }
}
