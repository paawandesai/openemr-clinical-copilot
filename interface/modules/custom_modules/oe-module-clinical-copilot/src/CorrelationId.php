<?php

/**
 * The id that ties one chart open to one trace on the agent: minted when the
 * Documents & Sources card renders, carried back by the card on every
 * upload, status poll and question, and sent to the agent as
 * `X-Correlation-ID` (agent/app/middleware/correlation.py). The agent logs
 * every event of the request under it, so upload, extraction and ask for
 * the same chart open read as one encounter.
 *
 * A UUID and nothing else. It comes back from the browser, and the agent
 * writes it into every log line, so a value that could carry text is
 * refused here rather than trusted to be refused there.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final readonly class CorrelationId
{
    public const HEADER = 'X-Correlation-ID';

    private const PATTERN = '/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/';

    /**
     * @throws \DomainException If the value is not a lowercase canonical UUID.
     */
    public function __construct(public string $value)
    {
        if (preg_match(self::PATTERN, $value) !== 1) {
            throw new \DomainException('Correlation id must be a lowercase canonical UUID');
        }
    }

    /** A random (version 4) UUID. */
    public static function mint(): self
    {
        $bytes = random_bytes(16);
        $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
        $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        $hex = bin2hex($bytes);
        return new self(sprintf(
            '%s-%s-%s-%s-%s',
            substr($hex, 0, 8),
            substr($hex, 8, 4),
            substr($hex, 12, 4),
            substr($hex, 16, 4),
            substr($hex, 20, 12),
        ));
    }
}
