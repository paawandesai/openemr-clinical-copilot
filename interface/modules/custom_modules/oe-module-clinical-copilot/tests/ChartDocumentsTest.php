<?php

/**
 * Filing an upload in the chart: once per chart (invariant 6, D1's digest
 * key), under a lock, and never on a second chart (D40 item 8). And moving a
 * wrong-patient upload to quarantine: once, with a note, never deleted (D60).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests;

use OpenEMR\Modules\ClinicalCopilot\BriefFailure;
use OpenEMR\Modules\ClinicalCopilot\BriefUnavailable;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\FakeChartDocumentIndex as FakeIndex;
use OpenEMR\Modules\ClinicalCopilot\Tests\Fakes\RecordingDocumentStorage;
use OpenEMR\Modules\ClinicalCopilot\W2\AgentDocumentId;
use OpenEMR\Modules\ClinicalCopilot\W2\ChartDocuments;
use OpenEMR\Modules\ClinicalCopilot\W2\DocumentKind;
use OpenEMR\Modules\ClinicalCopilot\W2\QuarantineOutcome;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;

final class ChartDocumentsTest extends TestCase
{
    private const PDF = "%PDF-1.7\nlab report\n%%EOF\n";
    private const PID = 49;
    private const TRACE = '8f7c2f0e-3b9a-4c1d-9e6f-0a1b2c3d4e5f';

    public function testANewFileIsStoredOnceAndTheLockIsReleased(): void
    {
        $storage = new RecordingDocumentStorage();
        $index = new FakeIndex();

        $filed = self::documents($storage, $index)->attach(self::PID, DocumentKind::LabPdf, self::digest(), 'report.pdf', self::PDF, 7);

        $this->assertSame(2474, $filed->documentId);
        $this->assertTrue($filed->created);
        $this->assertSame(1, $storage->stored);
        $this->assertSame(['lock', 'unlock'], $index->lockEvents);
    }

    public function testTheSameFileOnTheSameChartIsTheDocumentAlreadyThere(): void
    {
        $storage = new RecordingDocumentStorage();
        $index = new FakeIndex(existing: 1812);

        $filed = self::documents($storage, $index)->attach(self::PID, DocumentKind::IntakeForm, self::digest(), 'form.pdf', self::PDF, 7);

        $this->assertSame(1812, $filed->documentId);
        $this->assertFalse($filed->created);
        $this->assertSame(0, $storage->stored);
    }

    public function testAFileFiledOnAnotherChartIsRefusedAndNothingIsStored(): void
    {
        $storage = new RecordingDocumentStorage();
        $index = new FakeIndex(elsewhere: true);

        try {
            self::documents($storage, $index)->attach(self::PID, DocumentKind::LabPdf, self::digest(), 'report.pdf', self::PDF, 7);
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame(BriefFailure::DocumentAttachedElsewhere, $e->failure);
            $this->assertSame(409, $e->failure->httpStatus());
        }
        $this->assertSame(0, $storage->stored);
        $this->assertSame(['lock', 'unlock'], $index->lockEvents);
    }

    public function testTheElsewhereCheckRunsBeforeTheAgentIsAsked(): void
    {
        $this->expectException(BriefUnavailable::class);
        self::documents(new RecordingDocumentStorage(), new FakeIndex(elsewhere: true))
            ->assertNotFiledElsewhere(self::PID, self::digest());
    }

    public function testNoLockNoStore(): void
    {
        $storage = new RecordingDocumentStorage();
        try {
            self::documents($storage, new FakeIndex(lockable: false))
                ->attach(self::PID, DocumentKind::LabPdf, self::digest(), 'report.pdf', self::PDF, 7);
            $this->fail('Expected BriefUnavailable');
        } catch (BriefUnavailable $e) {
            $this->assertSame(BriefFailure::DocumentNotStored, $e->failure);
        }
        $this->assertSame(0, $storage->stored);
    }

    public function testBytesThatAreNotTheDigestAreAProgrammingError(): void
    {
        $this->expectException(\LogicException::class);
        self::documents(new RecordingDocumentStorage(), new FakeIndex())
            ->attach(self::PID, DocumentKind::LabPdf, new AgentDocumentId(str_repeat('0', 128)), 'report.pdf', self::PDF, 7);
    }

    public function testTheElsewhereCheckCoversTheQuarantineCategory(): void
    {
        // A quarantined document is still filed on its chart (D60).
        $index = new FakeIndex();
        self::documents(new RecordingDocumentStorage(), $index)->assertNotFiledElsewhere(self::PID, self::digest());
        $this->assertSame([[self::PID, [2, 3, 34]]], $index->elsewhereQueries);
    }

    public function testAMismatchedDocumentIsMovedToQuarantineWithANoteAndNotDeleted(): void
    {
        $storage = new RecordingDocumentStorage();
        $index = new FakeIndex(existing: 1812);

        $result = self::documents($storage, $index)->quarantine(self::PID, self::digest(), self::TRACE, 7);

        $this->assertSame(QuarantineOutcome::Quarantined, $result->outcome);
        $this->assertSame(1812, $result->documentId);
        $this->assertTrue($result->moved);
        $this->assertSame([[1812, 34]], $index->moves);
        $this->assertCount(1, $index->notes);
        [$noted, $owner, $note] = $index->notes[0];
        $this->assertSame([1812, 7], [$noted, $owner]);
        $this->assertStringContainsString('does not match this chart', $note);
        $this->assertStringContainsString('not deleted', $note);
        $this->assertStringEndsWith(self::TRACE, $note);
        $this->assertLessThanOrEqual(255, strlen($note), 'notes.note is a VARCHAR(255)');
        $this->assertSame(0, $storage->stored);
        $this->assertSame([], $storage->discarded);
        $this->assertSame(['lock', 'unlock'], $index->lockEvents);
    }

    public function testQuarantineIsIdempotentOnTheDigest(): void
    {
        $index = new FakeIndex(existing: 1812, categoriesOfExisting: [34]);

        $result = self::documents(new RecordingDocumentStorage(), $index)->quarantine(self::PID, self::digest(), self::TRACE, 7);

        $this->assertSame(QuarantineOutcome::Quarantined, $result->outcome);
        $this->assertFalse($result->moved);
        $this->assertSame([], $index->moves);
        $this->assertSame([], $index->notes);
    }

    public function testNothingOnThisChartIsNothingToMove(): void
    {
        $index = new FakeIndex(existing: null);
        $result = self::documents(new RecordingDocumentStorage(), $index)->quarantine(self::PID, self::digest(), self::TRACE, 7);
        $this->assertSame(QuarantineOutcome::NotOnChart, $result->outcome);
        $this->assertSame([], $index->moves);
    }

    /**
     * @return array<string, array{RecordingDocumentStorage, FakeIndex}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function quarantineFailures(): array
    {
        return [
            'no quarantine category on this instance' => [
                new RecordingDocumentStorage(categories: ['/Categories/Lab_Report' => 2]),
                new FakeIndex(existing: 1812),
            ],
            'no lock' => [new RecordingDocumentStorage(), new FakeIndex(existing: 1812, lockable: false)],
            'the move fails' => [new RecordingDocumentStorage(), new FakeIndex(existing: 1812, moveFails: true)],
        ];
    }

    #[DataProvider('quarantineFailures')]
    public function testAQuarantineThatCannotHappenSaysFailedAndLeavesTheDocumentFiled(RecordingDocumentStorage $storage, FakeIndex $index): void
    {
        $result = self::documents($storage, $index)->quarantine(self::PID, self::digest(), self::TRACE, 7);

        $this->assertSame(QuarantineOutcome::Failed, $result->outcome);
        $this->assertFalse($result->moved);
        $this->assertSame([], $index->moves);
        $this->assertSame([], $index->notes);
        $this->assertSame(0, $storage->stored);
    }

    private static function documents(RecordingDocumentStorage $storage, FakeIndex $index): ChartDocuments
    {
        return new ChartDocuments($storage, $index, new NullLogger());
    }

    private static function digest(): AgentDocumentId
    {
        return AgentDocumentId::ofBytes(self::PDF);
    }
}
