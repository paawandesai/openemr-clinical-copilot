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

use OpenEMR\Modules\ClinicalCopilot\WriteBack\InvalidWriteBackRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabValue;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackResult;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

/**
 * How a document's value is compared with a value the chart already holds
 * (work order 34). Exact, per D24: spelling of the same number or term is
 * not a difference; a different number, comparator, term or unit is.
 */
final class ChartMatchTest extends TestCase
{
    /** @return array<string, array{array<string, mixed>, string, bool}> */
    public static function values(): array
    {
        $numeric = static fn(float $value, ?string $comparator = null): array =>
            ['kind' => 'numeric', 'value' => $value] + ($comparator === null ? [] : ['comparator' => $comparator]);
        return [
            'the same number' => [$numeric(89.0), '89', true],
            'the same number with trailing zeros' => [$numeric(4.1), '4.10', true],
            'the same number with surrounding space' => [$numeric(4.1), ' 4.1 ', true],
            'a different number' => [$numeric(4.1), '4.2', false],
            'a nearly equal number' => [$numeric(4.1), '4.11', false],
            'a censored value against the same censored value' => [$numeric(5.0, '<'), '<5', true],
            'a censored value against an exact one' => [$numeric(5.0, '<'), '5', false],
            'an exact value against a censored one' => [$numeric(5.0), '<5', false],
            'a censored value with a space' => [$numeric(5.0, '<'), '< 5', true],
            'a different comparator' => [$numeric(5.0, '<'), '<=5', false],
            'a number against text' => [$numeric(5.0), 'five', false],
            'a number against an empty result' => [$numeric(5.0), '', false],
            'the same term' => [['kind' => 'qualitative', 'text' => 'Negative'], 'negative', true],
            'the same term with repeated spaces' => [['kind' => 'qualitative', 'text' => 'not  detected'], 'Not detected', true],
            'a different term' => [['kind' => 'qualitative', 'text' => 'negative'], 'positive', false],
            'a term against a number' => [['kind' => 'qualitative', 'text' => '5'], '5', true],
        ];
    }

    /** @param array<string, mixed> $value */
    #[DataProvider('values')]
    public function testAValueMatchesAStoredResultExactly(array $value, string $stored, bool $expected): void
    {
        $this->assertSame($expected, LabValue::fromArray($value, 'value')->matches($stored));
    }

    /** @return array<string, array{?string, string, bool}> */
    public static function units(): array
    {
        return [
            'the same unit' => ['mg/dL', 'mg/dL', true],
            'the same unit in another case' => ['mg/dL', 'MG/DL', true],
            'the same unit with space around it' => ['mmol/L', ' mmol/L ', true],
            'a different unit' => ['mg/dL', 'mmol/L', false],
            'no unit on either side' => [null, '', true],
            'a unit against none' => ['mg/dL', '', false],
            'none against a unit' => [null, 'mg/dL', false],
        ];
    }

    #[DataProvider('units')]
    public function testUnitsDifferOnlyInSubstance(?string $unit, string $stored, bool $expected): void
    {
        $this->assertSame($expected, LabValue::sameUnit($unit, $stored));
    }

    public function testACollectionTimeOnTheCollectionDateIsCarried(): void
    {
        $result = LabWriteBackResult::fromArray(self::row(['collected_at' => '2026-09-20T07:12:00']), self::DIGEST, 'results[0]');
        $this->assertSame('2026-09-20 07:12:00', $result->collectedAt?->format('Y-m-d H:i:s'));
    }

    public function testNoCollectionTimeIsNoCollectionTime(): void
    {
        $this->assertNull(LabWriteBackResult::fromArray(self::row(), self::DIGEST, 'results[0]')->collectedAt);
        $this->assertNull(LabWriteBackResult::fromArray(self::row(['collected_at' => null]), self::DIGEST, 'results[0]')->collectedAt);
    }

    public function testACollectionTimeOnAnotherDayIsRefused(): void
    {
        try {
            LabWriteBackResult::fromArray(self::row(['collected_at' => '2026-09-21T07:12:00']), self::DIGEST, 'results[0]');
            $this->fail('expected the row to be refused');
        } catch (InvalidWriteBackRequest $refused) {
            $this->assertSame('results[0].collected_at', $refused->field);
            $this->assertStringContainsString('time on collection_date', $refused->getMessage());
        }
    }

    private const DIGEST = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
        . '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';

    /**
     * @param array<string, mixed> $overrides
     * @return array<string, mixed>
     */
    private static function row(array $overrides = []): array
    {
        $key = self::DIGEST . ':2345-7:2026-09-20';
        return $overrides + [
            'test_code' => '2345-7',
            'test_name' => 'Glucose',
            'value' => ['kind' => 'numeric', 'value' => 89.0],
            'unit' => 'mg/dL',
            'reference_range' => ['low' => 70.0, 'high' => 99.0, 'text' => '70 - 99'],
            'collection_date' => '2026-09-20',
            'abnormal' => 'normal',
            'page' => 1,
            'bbox' => ['page' => 1, 'x0' => 1.0, 'y0' => 2.0, 'x1' => 3.0, 'y1' => 4.0],
            'idempotency_key' => $key,
            'extraction_id' => 'w2x-' . substr(hash('sha3-256', $key), 0, 32),
        ];
    }
}
