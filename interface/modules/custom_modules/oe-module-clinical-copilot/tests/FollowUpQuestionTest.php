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

use OpenEMR\Modules\ClinicalCopilot\FollowUpQuestion;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class FollowUpQuestionTest extends TestCase
{
    public function testTrimsTheQuestion(): void
    {
        $this->assertSame('When was that prescribed?', (new FollowUpQuestion("  When was that prescribed?\n"))->value);
    }

    public function testCountsCharactersNotBytes(): void
    {
        $this->assertSame(FollowUpQuestion::MAX_LENGTH, mb_strlen((new FollowUpQuestion(str_repeat('é', FollowUpQuestion::MAX_LENGTH)))->value));
    }

    #[DataProvider('rejectedQuestions')]
    public function testRejects(string $text): void
    {
        $this->expectException(\DomainException::class);
        new FollowUpQuestion($text);
    }

    /**
     * @return array<string, array{string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function rejectedQuestions(): array
    {
        return [
            'empty' => [''],
            'only whitespace' => [" \n\t"],
            'over the agent limit' => [str_repeat('a', FollowUpQuestion::MAX_LENGTH + 1)],
        ];
    }

    public function testIsNotShownInDebugOutput(): void
    {
        $this->assertStringNotContainsString('creatinine', print_r(new FollowUpQuestion('most recent creatinine?'), true));
    }
}
