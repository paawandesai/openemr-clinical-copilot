<?php

/**
 * D1's idempotency key, and the extraction id derived from it.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use DateTimeImmutable;

/**
 * The PHP half of `agent/app/models/w2/writeback.py`'s key functions. Both
 * sides derive the same two strings from the same three inputs, and the
 * endpoint recomputes what the caller sent rather than trusting it — a
 * caller that composed the key differently gets a 400 naming the field,
 * not a second row in a chart.
 *
 * The extraction id is a pure function of the key, which is what makes the
 * duplicate the ledger refuses the same duplicate already visible in
 * `Observation.note[0].text`. Duplicate detection has to work from the
 * chart side too: the FHIR Observation search has no `note`, `_text` or
 * `identifier` parameter, so a client narrows on patient + code
 * server-side and matches this string in the results
 * (docs/w2/spikes/writeback.md §7).
 */
final class IdempotencyKeys
{
    /** A colon, matching the `<hash>:<code>:<date>` form the spike read back intact. */
    private const SEPARATOR = ':';

    /** Lowercase hex, 512 bits: OpenEMR hashes uploaded documents with SHA3-512. */
    public const SHA3_512_PATTERN = '/^[0-9a-f]{128}$/';

    public const EXTRACTION_ID_PATTERN = '/^w2x-[0-9a-f]{32}$/';

    public const PATIENT_UUID_PATTERN = '/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/';

    public const CORRELATION_ID_PATTERN =
        '/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/';

    /**
     * The document-level key: OpenEMR's own digest of the file.
     *
     * Normalises case and refuses anything that is not a SHA3-512 digest,
     * because the one thing that must never happen is a SHA-256 being
     * compared against a SHA3-512 and concluding the document is new.
     *
     * @throws InvalidWriteBackRequest
     */
    public static function documentKey(string $sha3512, string $field = 'document.sha3_512'): string
    {
        $normalised = strtolower(trim($sha3512));
        if (preg_match(self::SHA3_512_PATTERN, $normalised) !== 1) {
            throw new InvalidWriteBackRequest(
                $field,
                'is a SHA3-512 digest, 128 lowercase hex characters'
            );
        }
        return $normalised;
    }

    /**
     * D1's UNIQUE key over (document sha3-512, test code, collection date).
     *
     * Canonicalising here rather than at each call site is the point: the
     * same result submitted as `2345-7` and as ` 2345-7 ` is one row, not
     * two.
     *
     * @throws InvalidWriteBackRequest
     */
    public static function labResultKey(
        string $documentSha3512,
        string $testCode,
        DateTimeImmutable $collectionDate
    ): string {
        if (trim($testCode) === '') {
            throw new InvalidWriteBackRequest('results[0].test_code', 'is required');
        }
        return implode(self::SEPARATOR, [
            self::documentKey($documentSha3512),
            strtoupper(trim($testCode)),
            $collectionDate->format('Y-m-d'),
        ]);
    }

    /**
     * The id that goes into `procedure_result.comments`, and nothing else
     * goes there.
     *
     * Short and opaque on purpose: that column is clinician-visible text in
     * a chart, so it has to be parseable without being alarming. `w2x-`
     * plus 128 bits of hex is both, where the raw 128-character document
     * digest would be neither. The ledger, not this id, is what enforces
     * idempotency; this is how the write is found again from the chart.
     */
    public static function labResultExtractionId(
        string $documentSha3512,
        string $testCode,
        DateTimeImmutable $collectionDate
    ): string {
        $key = self::labResultKey($documentSha3512, $testCode, $collectionDate);
        return 'w2x-' . substr(hash('sha3-256', $key), 0, 32);
    }
}
