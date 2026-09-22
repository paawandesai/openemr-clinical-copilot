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

use DateTimeImmutable;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\AbnormalFlag;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\IdempotencyKeys;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\InvalidWriteBackRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabValue;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackRequest;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\ReferenceRange;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

/**
 * The write-back endpoint's pure logic: no database, no HTTP.
 *
 * The first test is the one that matters most. The idempotency key and the
 * extraction id are computed independently on both sides of the wire — in
 * Python by agent/app/models/w2/writeback.py, in PHP by IdempotencyKeys —
 * and the endpoint rejects a request whose key does not match what it
 * recomputes. If the two derivations ever drift, every write becomes a 400
 * and no amount of PHP-only testing would say why. So the expected values
 * below were produced by running the committed Python contract, not by
 * running this code.
 */
final class WriteBackContractTest extends TestCase
{
    /** SHA3-512 of the lab PDF used for the checkpoint evidence. */
    private const DIGEST = 'd67f87ee7dda5e297f56e4b12eebd8309ec30cd4525fb75c7a3ab5dc193f4b9a'
        . 'f6595fe3c73fdc42a69a922b6e746bb3573cb0c63aca7a003612e864969e0310';

    /** From agent/app/models/w2/writeback.py on feat/w2-contracts. */
    private const PYTHON_KEY = self::DIGEST . ':2345-7:2026-09-20';
    private const PYTHON_EXTRACTION_ID = 'w2x-e962aba60f40d8c74f9c24351ba59b3c';

    public function testDerivationMatchesTheAgentSideContract(): void
    {
        $date = new DateTimeImmutable('2026-09-20');

        $this->assertSame(
            self::PYTHON_KEY,
            IdempotencyKeys::labResultKey(self::DIGEST, '2345-7', $date)
        );
        $this->assertSame(
            self::PYTHON_EXTRACTION_ID,
            IdempotencyKeys::labResultExtractionId(self::DIGEST, '2345-7', $date)
        );
    }

    public function testKeyIsCanonicalSoOneResultCannotBecomeTwoRows(): void
    {
        $date = new DateTimeImmutable('2026-09-20');

        $this->assertSame(
            IdempotencyKeys::labResultKey(self::DIGEST, '2345-7', $date),
            IdempotencyKeys::labResultKey(strtoupper(self::DIGEST), ' 2345-7 ', $date)
        );
    }

    public function testADigestFromTheWrongAlgorithmIsRefused(): void
    {
        // A SHA-256, which is what the document factory records. Comparing
        // one against a SHA3-512 would conclude every document is new.
        $this->expectException(InvalidWriteBackRequest::class);
        IdempotencyKeys::documentKey(str_repeat('a', 64));
    }

    public function testIndeterminateIsNotWrittenBack(): void
    {
        $this->expectException(InvalidWriteBackRequest::class);
        AbnormalFlag::INDETERMINATE->toOpenEmrCode();
    }

    /** @return array<string, array{AbnormalFlag, string}> */
    public static function decidableFlags(): array
    {
        return [
            'normal' => [AbnormalFlag::NORMAL, 'no'],
            'high' => [AbnormalFlag::HIGH, 'high'],
            'low' => [AbnormalFlag::LOW, 'low'],
            'abnormal' => [AbnormalFlag::ABNORMAL, 'yes'],
        ];
    }

    #[DataProvider('decidableFlags')]
    public function testFlagsMapOntoTheProcResAbnormalList(AbnormalFlag $flag, string $expected): void
    {
        $this->assertSame($expected, $flag->toOpenEmrCode());
    }

    public function testACensoredValueIsNotStoredAsNumeric(): void
    {
        $censored = LabValue::fromArray(['kind' => 'numeric', 'value' => 5.0, 'comparator' => '<'], 'v');

        // OpenEMR's FHIR read decides valueQuantity vs valueString with
        // is_numeric() on this column, and '<5' is not numeric.
        $this->assertSame('<5', $censored->toOpenEmrResult());
        $this->assertSame('S', $censored->toOpenEmrDataType());

        $plain = LabValue::fromArray(['kind' => 'numeric', 'value' => 142.0], 'v');
        $this->assertSame('142', $plain->toOpenEmrResult());
        $this->assertSame('N', $plain->toOpenEmrDataType());
    }

    public function testAQualitativeValueKeepsItsText(): void
    {
        $value = LabValue::fromArray(['kind' => 'qualitative', 'text' => 'not detected'], 'v');

        $this->assertSame('not detected', $value->toOpenEmrResult());
        $this->assertSame('S', $value->toOpenEmrDataType());
    }

    public function testAValueArrivingAsAStringIsRefusedRatherThanCoerced(): void
    {
        $this->expectException(InvalidWriteBackRequest::class);
        LabValue::fromArray(['kind' => 'numeric', 'value' => '142'], 'v');
    }

    public function testOnlyATwoBoundedRangeRoundTripsThroughFhir(): void
    {
        $both = ReferenceRange::fromArray(
            ['low' => 70.0, 'high' => 99.0, 'text' => '70 - 99'],
            'r'
        );
        $this->assertSame('70-99', $both->toOpenEmrRange());

        // One bound: OpenEMR cannot parse it into a referenceRange, so the
        // printed text is stored rather than a bound this code invented.
        $oneSided = ReferenceRange::fromArray(['high' => 200.0, 'text' => '< 200'], 'r');
        $this->assertSame('< 200', $oneSided->toOpenEmrRange());
    }

    public function testIntakeDocumentsAreRefused(): void
    {
        $this->expectExceptionMessageMatches('/never merged into it/');
        LabWriteBackRequest::fromArray($this->requestPayload(['document_kind' => 'intake_form']));
    }

    public function testAKeyTheEndpointDidNotComposeIsRefused(): void
    {
        $payload = $this->requestPayload();
        $payload['results'][0]['idempotency_key'] = self::DIGEST . ':9999-9:2026-09-20';

        $this->expectException(InvalidWriteBackRequest::class);
        LabWriteBackRequest::fromArray($payload);
    }

    public function testAnUnknownFieldIsRefusedRatherThanIgnored(): void
    {
        $payload = $this->requestPayload();
        $payload['results'][0]['confidence'] = 0.97;

        $this->expectExceptionMessageMatches('/unknown field/');
        LabWriteBackRequest::fromArray($payload);
    }

    public function testSendingBothBytesAndADocumentIdIsRefused(): void
    {
        $payload = $this->bytesPayload();
        $payload['document']['existing_document_id'] = 7;

        $this->expectExceptionMessageMatches('/exactly one/');
        LabWriteBackRequest::fromArray($payload);
    }

    public function testADigestThatIsNotTheContentIsRefused(): void
    {
        $payload = $this->bytesPayload();
        $payload['document']['content_base64'] = base64_encode('some other bytes entirely');

        $this->expectExceptionMessageMatches('/SHA3-512 of the decoded/');
        LabWriteBackRequest::fromArray($payload);
    }

    public function testAWellFormedRequestParses(): void
    {
        $request = LabWriteBackRequest::fromArray($this->requestPayload());

        $this->assertSame(self::PYTHON_KEY, $request->onlyResult()->idempotencyKey);
        $this->assertSame(self::PYTHON_EXTRACTION_ID, $request->onlyResult()->extractionId);
        $this->assertSame(self::DIGEST, $request->documentKey());
    }

    /**
     * The same request with the bytes attached instead of a document id, so
     * the content checks have content to check.
     *
     * @return array<string, mixed>
     */
    private function bytesPayload(): array
    {
        $content = 'x';
        $digest = hash('sha3-512', $content);
        $key = $digest . ':2345-7:2026-09-20';

        $payload = $this->requestPayload();
        $payload['document'] = [
            'filename' => 'lab.pdf',
            'mime_type' => 'application/pdf',
            'sha3_512' => $digest,
            'byte_length' => strlen($content),
            'content_base64' => base64_encode($content),
        ];
        $payload['results'][0]['idempotency_key'] = $key;
        $payload['results'][0]['extraction_id'] = 'w2x-' . substr(hash('sha3-256', $key), 0, 32);
        return $payload;
    }

    /**
     * @param array<string, mixed> $overrides
     * @return array<string, mixed>
     */
    private function requestPayload(array $overrides = []): array
    {
        $payload = [
            'patient_uuid' => 'a2c36e54-af42-4c38-b86e-ade616ed5494',
            'document' => [
                'filename' => 'lab.pdf',
                'mime_type' => 'application/pdf',
                'sha3_512' => self::DIGEST,
                'byte_length' => 724,
                'existing_document_id' => 2474,
            ],
            'results' => [[
                'test_code' => '2345-7',
                'test_name' => 'Glucose',
                'value' => ['kind' => 'numeric', 'value' => 142.0],
                'unit' => 'mg/dL',
                'reference_range' => ['low' => 70.0, 'high' => 99.0, 'text' => '70 - 99'],
                'collection_date' => '2026-09-20',
                'abnormal' => 'high',
                'page' => 1,
                'bbox' => ['page' => 1, 'x0' => 306.0, 'y0' => 256.398, 'x1' => 338.5, 'y1' => 268.24],
                'idempotency_key' => self::PYTHON_KEY,
                'extraction_id' => self::PYTHON_EXTRACTION_ID,
            ]],
            'extraction_id' => 'w2x-' . substr(hash('sha3-256', self::DIGEST . ':v1'), 0, 32),
            'correlation_id' => '11111111-2222-4333-8444-555555555555',
        ];
        return array_merge($payload, $overrides);
    }
}
