<?php

/**
 * The preview's two forms of `document_id` — OpenEMR's integer and a Week 2
 * citation's SHA3-512 digest — are checked identically: the same ACL, the
 * same "belongs to the open chart" rule, and the same 404 for another
 * patient's document. The digest form was signed off on that condition.
 *
 * The reader's query goes through an injected function here. The fake table
 * below answers the query's own predicate — key column, the bound open-chart
 * pid, not deleted — so a 404 comes from the chart check, which the positive
 * controls prove by finding the same rows when the right chart is open.
 *
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
use Psr\Log\NullLogger;
use Symfony\Component\HttpFoundation\Request;

final class DocumentPreviewParityTest extends TestCase
{
    /** Patient 38's lab report. */
    private const PDF_ID = 2482;
    private const PDF_HASH = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

    /** Patient 38's non-PDF, the positive control: found, then refused as not previewable. */
    private const TEXT_ID = 2490;
    private const TEXT_HASH = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

    /** Patient 38's deleted PDF. */
    private const DELETED_ID = 2491;
    private const DELETED_HASH = 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';

    private const OWNER = '38';
    private const OTHER_CHART = '49';

    /**
     * @return array<string, array{string, string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function bothForms(): array
    {
        return [
            'integer' => [(string) self::PDF_ID, (string) self::TEXT_ID],
            'digest' => [self::PDF_HASH, self::TEXT_HASH],
        ];
    }

    public function testAnotherPatientsDocumentIsTheSame404ByIntegerAndByDigest(): void
    {
        $byInteger = self::failureReading((string) self::PDF_ID, self::OTHER_CHART);
        $byDigest = self::failureReading(self::PDF_HASH, self::OTHER_CHART);

        $this->assertSame(DocumentFailure::NotFound, $byInteger);
        $this->assertSame($byInteger, $byDigest);
        // document.php answers from the failure alone, so the two responses
        // are the same status and the same body.
        $this->assertSame(404, $byDigest->httpStatus());
        $this->assertSame($byInteger->message(), $byDigest->message());
    }

    #[DataProvider('bothForms')]
    public function testTheSameRowsAreFoundWhenTheirOwnChartIsOpen(string $pdf, string $text): void
    {
        // Positive control: the fake finds patient 38's row for patient 38,
        // so the 404 above is the chart check and not a fake that finds
        // nothing. The non-PDF row is the control because a found PDF goes on
        // to open the real \Document, which needs a database; the same query
        // found it, and the same query refuses it from another chart.
        $this->assertSame(DocumentFailure::NotPreviewable, self::failureReading($text, self::OWNER));
        $this->assertSame(DocumentFailure::NotFound, self::failureReading($text, self::OTHER_CHART));
        $this->assertSame(DocumentFailure::NotFound, self::failureReading($pdf, self::OTHER_CHART));
    }

    public function testADeletedDocumentIsTheSame404ByIntegerAndByDigest(): void
    {
        $this->assertSame(DocumentFailure::NotFound, self::failureReading((string) self::DELETED_ID, self::OWNER));
        $this->assertSame(DocumentFailure::NotFound, self::failureReading(self::DELETED_HASH, self::OWNER));
    }

    public function testBothFormsRunOneQueryScopedToTheOpenChart(): void
    {
        [$byIdSql, $byIdBinds] = SourceDocumentReader::rowQuery(self::request((string) self::PDF_ID, self::OTHER_CHART));
        [$byHashSql, $byHashBinds] = SourceDocumentReader::rowQuery(self::request(self::PDF_HASH, self::OTHER_CHART));

        // One template; only the key column differs.
        $this->assertSame($byIdSql, str_replace('`hash` = ?', '`id` = ?', $byHashSql));
        $this->assertStringContainsString('`foreign_id` = ? AND `deleted` = 0', $byHashSql);
        // The chart bound is the session's open patient, never a request field.
        $this->assertSame([self::PDF_ID, 49], $byIdBinds);
        $this->assertSame([self::PDF_HASH, 49], $byHashBinds);
    }

    #[DataProvider('bothForms')]
    public function testTheAclIsCheckedFirstAndTheSameWay(string $pdf, string $text): void
    {
        $consulted = 0;
        try {
            DocumentReadRequest::fromRequest(self::get($pdf), self::OWNER, static function () use (&$consulted): bool {
                $consulted++;
                return false;
            });
            $this->fail('Expected DocumentUnavailable');
        } catch (DocumentUnavailable $e) {
            $this->assertSame(DocumentFailure::Forbidden, $e->failure);
        }
        $this->assertSame(1, $consulted);
    }

    #[DataProvider('bothForms')]
    public function testNoOpenChartIsRefusedTheSameWay(string $pdf, string $text): void
    {
        try {
            DocumentReadRequest::fromRequest(self::get($pdf), null, static fn (): bool => true);
            $this->fail('Expected DocumentUnavailable');
        } catch (DocumentUnavailable $e) {
            $this->assertSame(DocumentFailure::NoOpenChart, $e->failure);
        }
    }

    private static function failureReading(string $documentId, string $openChart): DocumentFailure
    {
        try {
            (new SourceDocumentReader(new NullLogger(), self::table(...)))->read(self::request($documentId, $openChart));
        } catch (DocumentUnavailable $e) {
            return $e->failure;
        }
        throw new \LogicException('Expected the read to be refused before the document is opened');
    }

    /**
     * The documents table, answering the reader's query by its own predicate.
     *
     * @param list<int|string|null> $binds
     * @return array{id: int, name: string, mimetype: string}|null
     */
    private static function table(string $sql, array $binds): ?array
    {
        $rows = [
            ['id' => self::PDF_ID, 'hash' => self::PDF_HASH, 'foreign_id' => 38, 'deleted' => 0, 'name' => 'lab.pdf', 'mimetype' => 'application/pdf'],
            ['id' => self::TEXT_ID, 'hash' => self::TEXT_HASH, 'foreign_id' => 38, 'deleted' => 0, 'name' => 'note.txt', 'mimetype' => 'text/plain'],
            ['id' => self::DELETED_ID, 'hash' => self::DELETED_HASH, 'foreign_id' => 38, 'deleted' => 1, 'name' => 'old.pdf', 'mimetype' => 'application/pdf'],
        ];
        if (preg_match('/WHERE `(id|hash)` = \? AND `foreign_id` = \? AND `deleted` = 0/', $sql, $match) !== 1) {
            throw new \LogicException('The reader asked a query this table does not answer');
        }
        [$key, $pid] = $binds;
        foreach ($rows as $row) {
            if ($row[$match[1]] === $key && $row['foreign_id'] === $pid && $row['deleted'] === 0) {
                return ['id' => $row['id'], 'name' => $row['name'], 'mimetype' => $row['mimetype']];
            }
        }
        return null;
    }

    private static function request(string $documentId, string $openChart): DocumentReadRequest
    {
        return DocumentReadRequest::fromRequest(self::get($documentId), $openChart, static fn (): bool => true);
    }

    private static function get(string $documentId): Request
    {
        return Request::create('/document.php', 'GET', ['document_id' => $documentId]);
    }
}
