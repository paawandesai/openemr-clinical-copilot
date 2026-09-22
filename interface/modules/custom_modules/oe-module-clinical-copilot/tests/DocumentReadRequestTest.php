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

use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentFailure;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentReadRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\DocumentUnavailable;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\SourceDocumentReader;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\Request;

/**
 * The guard on the source-document preview. It replaces
 * `GET /fhir/Binary/:id`, which a physician cannot call (it wants the
 * `admin`/`users` ACL), so these are the checks that now stand between a
 * document id and a patient's lab report.
 */
final class DocumentReadRequestTest extends TestCase
{
    public function testAcceptsADocumentIdForThePatientWhoseChartIsOpen(): void
    {
        $read = DocumentReadRequest::fromRequest(self::get(['document_id' => '2474']), '1', self::allowed(...));

        $this->assertSame(2474, $read->documentId);
        $this->assertSame(1, $read->pid);
    }

    /**
     * @param array<string, string> $query
     */
    #[DataProvider('refusals')]
    public function testRefuses(string $method, array $query, mixed $sessionPid, bool $mayRead, DocumentFailure $expected): void
    {
        $request = Request::create('/document.php', $method, $query);
        try {
            DocumentReadRequest::fromRequest($request, $sessionPid, static fn (): bool => $mayRead);
            $this->fail('expected DocumentUnavailable');
        } catch (DocumentUnavailable $e) {
            $this->assertSame($expected, $e->failure);
        }
    }

    /**
     * @return array<string, array{string, array<string, string>, mixed, bool, DocumentFailure}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function refusals(): array
    {
        return [
            'a POST is not a read' => ['POST', ['document_id' => '2474'], '1', true, DocumentFailure::InvalidRequest],
            'no document id' => ['GET', [], '1', true, DocumentFailure::InvalidRequest],
            'a non-numeric id' => ['GET', ['document_id' => 'abc'], '1', true, DocumentFailure::InvalidRequest],
            'a zero id' => ['GET', ['document_id' => '0'], '1', true, DocumentFailure::InvalidRequest],
            'a negative id' => ['GET', ['document_id' => '-3'], '1', true, DocumentFailure::InvalidRequest],
            'without the documents ACL' => ['GET', ['document_id' => '2474'], '1', false, DocumentFailure::Forbidden],
            'with no chart open' => ['GET', ['document_id' => '2474'], null, true, DocumentFailure::NoOpenChart],
            'with a nonsense session pid' => ['GET', ['document_id' => '2474'], 'nope', true, DocumentFailure::NoOpenChart],
        ];
    }

    public function testTheAclIsCheckedBeforeTheOpenChart(): void
    {
        // Order matters for what a caller learns: someone without the ACL is
        // told they lack permission whether or not a chart is open, so the
        // reply cannot be used to probe session state.
        try {
            DocumentReadRequest::fromRequest(self::get(['document_id' => '2474']), null, static fn (): bool => false);
            $this->fail('expected DocumentUnavailable');
        } catch (DocumentUnavailable $e) {
            $this->assertSame(DocumentFailure::Forbidden, $e->failure);
        }
    }

    public function testEveryRefusalHasAStatusAndSaysNothingAboutTheDocument(): void
    {
        foreach (DocumentFailure::cases() as $failure) {
            $this->assertGreaterThanOrEqual(400, $failure->httpStatus());
            $this->assertNotSame('', $failure->message());
            // No id, no patient, no filename: a document id that exists for
            // a patient is itself a fact about that patient.
            $this->assertDoesNotMatchRegularExpression('/\d/', $failure->message());
        }
    }

    public function testNotFoundIsTheOneAnswerForEveryWayADocumentIsNotYours(): void
    {
        // Missing, another patient's, and deleted all map here on purpose.
        $this->assertSame(404, DocumentFailure::NotFound->httpStatus());
    }

    /** @return array<string, array{string, string}> */
    public static function filenames(): array
    {
        return [
            'plain' => ['lab-report.pdf', 'lab-report.pdf'],
            'a path is stripped' => ['../../etc/passwd', 'passwd'],
            'a windows path is stripped' => ['C:\\\\temp\\\\report.pdf', 'report.pdf'],
            'quotes cannot break the header' => ['a"b.pdf', 'a_b.pdf'],
            'a newline cannot inject a header' => ["a\r\nX-Evil: 1.pdf", 'a__X-Evil__1.pdf'],
            // One underscore per byte: the filter is byte-wise, because a
            // Content-Disposition filename is bytes on a header line.
            'unicode is replaced' => ['réport.pdf', 'r__port.pdf'],
            'an empty name gets a default' => ['', 'document.pdf'],
            'a dot-only name gets a default' => ['...', 'document.pdf'],
        ];
    }

    /**
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    #[DataProvider('filenames')]
    public function testTheContentDispositionFilenameIsSafe(string $stored, string $expected): void
    {
        $this->assertSame($expected, SourceDocumentReader::safeFilename($stored));
    }

    public function testALongFilenameIsTruncated(): void
    {
        $this->assertSame(100, strlen(SourceDocumentReader::safeFilename(str_repeat('a', 300) . '.pdf')));
    }

    /**
     * @param array<string, string> $query
     */
    private static function get(array $query): Request
    {
        return Request::create('/document.php', 'GET', $query);
    }

    private static function allowed(): bool
    {
        return true;
    }
}
