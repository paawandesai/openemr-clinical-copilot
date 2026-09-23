<?php

/**
 * Filing an upload in the chart: once per chart (invariant 6, D1's digest
 * key), under a lock, and never on a second chart (D40 item 8).
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
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;

final class ChartDocumentsTest extends TestCase
{
    private const PDF = "%PDF-1.7\nlab report\n%%EOF\n";
    private const PID = 49;

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

    private static function documents(RecordingDocumentStorage $storage, FakeIndex $index): ChartDocuments
    {
        return new ChartDocuments($storage, $index, new NullLogger());
    }

    private static function digest(): AgentDocumentId
    {
        return AgentDocumentId::ofBytes(self::PDF);
    }
}
