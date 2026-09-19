<?php

/**
 * The parts of the agent's ConversationTurnResponse (agent/app/models/api.py)
 * the chart card shows for one follow-up turn.
 *
 * As with the brief, what is verified, unverified, refused or missing is the
 * agent's decision and wording; nothing is re-derived here. This class only
 * checks the response's shape and keeps identifiers the card has no use for
 * (session_id, patient_session_key, patient_id) away from the browser.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

/**
 * @phpstan-type TurnClaim array{resource_type: string, source_id: string, field: string, verified: bool, unverified_reason: ?string, text: string}
 * @phpstan-type TurnRetrieval array{resource_type: string, rule: string, retrieved: int, considered: int}
 * @phpstan-type TurnConversation array{turn: int, transition: string, context_turns: int, context_claims: int, state_saved: bool, expires_in_seconds: int}
 * @phpstan-type TurnVerification array{claims: int, verified: int, unverified: int}
 */
final readonly class ConversationTurn
{
    /**
     * @param TurnConversation $conversation
     * @param TurnVerification $verification
     * @param list<TurnClaim> $claims
     * @param list<TurnRetrieval> $retrieval
     */
    private function __construct(
        public string $answer,
        public string $turnStatus,
        public array $conversation,
        public array $verification,
        public array $claims,
        public array $retrieval,
        public float $totalSeconds,
        public string $correlationId,
    ) {
    }

    /**
     * @param array<mixed> $body The decoded 200 response.
     * @throws \UnexpectedValueException If the body does not match the contract.
     */
    public static function fromAgentResponse(array $body): self
    {
        $conversation = self::map($body, 'conversation');
        $verification = self::map($body, 'verification');
        $counts = [
            'claims' => self::int($verification, 'claims'),
            'verified' => self::int($verification, 'verified'),
            'unverified' => self::int($verification, 'unverified'),
        ];

        $claims = [];
        foreach (self::rows($body, 'claims') as $claim) {
            $reason = $claim['unverified_reason'] ?? null;
            if ($reason !== null && !is_string($reason)) {
                throw new \UnexpectedValueException('Agent claim has a malformed unverified_reason');
            }
            $claims[] = [
                'resource_type' => self::string($claim, 'resource_type'),
                'source_id' => self::string($claim, 'source_id'),
                'field' => self::string($claim, 'field'),
                'verified' => self::bool($claim, 'verified'),
                'unverified_reason' => $reason,
                'text' => self::string($claim, 'text'),
            ];
        }
        // The summary is what the card's banner states; it must describe the claims shown.
        $verified = count(array_filter($claims, static fn (array $claim): bool => $claim['verified']));
        if ($counts['claims'] !== count($claims) || $counts['verified'] !== $verified || $counts['unverified'] !== count($claims) - $verified) {
            throw new \UnexpectedValueException('Agent verification summary is inconsistent with its claims');
        }

        $retrieval = [];
        foreach (self::rows($body, 'retrieval') as $read) {
            $retrieval[] = [
                'resource_type' => self::string($read, 'resource_type'),
                'rule' => self::string($read, 'rule'),
                'retrieved' => self::int($read, 'retrieved'),
                'considered' => self::int($read, 'considered'),
            ];
        }

        $totalSeconds = self::map($body, 'timing')['total_seconds'] ?? null;
        if (!is_int($totalSeconds) && !is_float($totalSeconds)) {
            throw new \UnexpectedValueException('Agent response has no timing.total_seconds');
        }

        return new self(
            self::string($body, 'answer'),
            self::string($body, 'turn_status'),
            [
                'turn' => self::int($conversation, 'turn'),
                'transition' => self::string($conversation, 'transition'),
                'context_turns' => self::int($conversation, 'context_turns'),
                'context_claims' => self::int($conversation, 'context_claims'),
                'state_saved' => self::bool($conversation, 'state_saved'),
                'expires_in_seconds' => self::int($conversation, 'expires_in_seconds'),
            ],
            $counts,
            $claims,
            $retrieval,
            (float) $totalSeconds,
            self::string($body, 'correlation_id'),
        );
    }

    /**
     * @return array{status: 'ok', answer: string, turn_status: string, conversation: TurnConversation, verification: TurnVerification, claims: list<TurnClaim>, retrieval: list<TurnRetrieval>, total_seconds: float, correlation_id: string}
     */
    public function toCardPayload(): array
    {
        return [
            'status' => 'ok',
            'answer' => $this->answer,
            'turn_status' => $this->turnStatus,
            'conversation' => $this->conversation,
            'verification' => $this->verification,
            'claims' => $this->claims,
            'retrieval' => $this->retrieval,
            'total_seconds' => $this->totalSeconds,
            'correlation_id' => $this->correlationId,
        ];
    }

    /**
     * @param array<mixed> $from
     */
    private static function string(array $from, string $key): string
    {
        $value = $from[$key] ?? null;
        if (!is_string($value) || $value === '') {
            throw new \UnexpectedValueException('Agent response field is not a non-empty string: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     */
    private static function int(array $from, string $key): int
    {
        $value = $from[$key] ?? null;
        if (!is_int($value) || $value < 0) {
            throw new \UnexpectedValueException('Agent response field is not a non-negative integer: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     */
    private static function bool(array $from, string $key): bool
    {
        $value = $from[$key] ?? null;
        if (!is_bool($value)) {
            throw new \UnexpectedValueException('Agent response field is not a boolean: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     * @return array<mixed>
     */
    private static function map(array $from, string $key): array
    {
        $value = $from[$key] ?? null;
        if (!is_array($value)) {
            throw new \UnexpectedValueException('Agent response field is not an object: ' . $key);
        }
        return $value;
    }

    /**
     * @param array<mixed> $from
     * @return list<array<mixed>>
     */
    private static function rows(array $from, string $key): array
    {
        $rows = [];
        foreach (self::map($from, $key) as $row) {
            if (!is_array($row)) {
                throw new \UnexpectedValueException('Agent response list holds a non-object: ' . $key);
            }
            $rows[] = $row;
        }
        return $rows;
    }
}
