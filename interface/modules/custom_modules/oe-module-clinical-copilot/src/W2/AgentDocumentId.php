<?php

/**
 * The id the agent gives an uploaded document: D1's document-level key,
 * which is the file's SHA3-512 digest in lowercase hex
 * (agent/app/models/w2/writeback.py `document_idempotency_key`).
 *
 * The same string OpenEMR stores in `documents.hash` for the same bytes,
 * which is what lets the preview route resolve a citation's document id to
 * the chart's own copy of the file without the browser holding any mapping.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

final readonly class AgentDocumentId
{
    public const PATTERN = '/^[0-9a-f]{128}$/';

    /**
     * @throws \DomainException If the value is not a lowercase SHA3-512 hex digest.
     */
    public function __construct(public string $value)
    {
        if (preg_match(self::PATTERN, $value) !== 1) {
            throw new \DomainException('Document id must be a SHA3-512 digest, 128 lowercase hex characters');
        }
    }

    public static function ofBytes(string $bytes): self
    {
        return new self(hash('sha3-512', $bytes));
    }

    /**
     * The ids a question is about, as the card posts them: at most
     * `$max`, each well formed, none repeated.
     *
     * @param mixed $raw The `document_ids` form field.
     * @return list<self>
     * @throws \DomainException
     */
    public static function listFrom(mixed $raw, int $max): array
    {
        if ($raw === null || $raw === '') {
            return [];
        }
        if (!is_array($raw) || !array_is_list($raw) || count($raw) > $max) {
            throw new \DomainException('document_ids must be a list of at most ' . $max . ' ids');
        }
        $ids = [];
        foreach ($raw as $value) {
            if (!is_string($value)) {
                throw new \DomainException('document_ids must hold strings');
            }
            $ids[$value] = new self($value);
        }
        if (count($ids) !== count($raw)) {
            throw new \DomainException('document_ids must not repeat an id');
        }
        return array_values($ids);
    }
}
