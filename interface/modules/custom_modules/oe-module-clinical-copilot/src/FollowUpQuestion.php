<?php

/**
 * A physician's follow-up question, within the agent's limits
 * (agent/app/models/api.py: FOLLOW_UP_QUESTION_MAX_LENGTH). Clinical free
 * text: never logged, stored or put in the audit comment.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final readonly class FollowUpQuestion
{
    public const MAX_LENGTH = 500;

    public string $value;

    public function __construct(#[\SensitiveParameter] string $text)
    {
        $text = trim($text);
        if ($text === '' || mb_strlen($text) > self::MAX_LENGTH) {
            throw new \DomainException('Question must be 1 to ' . self::MAX_LENGTH . ' characters');
        }
        $this->value = $text;
    }

    /**
     * @return array<string, string>
     */
    public function __debugInfo(): array
    {
        return ['value' => '[redacted]'];
    }
}
