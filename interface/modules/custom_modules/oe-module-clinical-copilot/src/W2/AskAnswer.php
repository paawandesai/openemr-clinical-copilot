<?php

/**
 * One turn of the Week 2 ask path, as the chart card receives it: the parts
 * of the agent's `AskResponse` (agent/app/models/w2/ask.py) the card draws,
 * checked for shape and for whom they are about.
 *
 * Nothing here is re-derived or re-worded. What is verified, unsupported,
 * refused or still processing is the agent's decision; `answer_view` is
 * passed to `renderAnswerView()` exactly as the agent built it, and that
 * renderer refuses a schema version it does not know.
 *
 * WHOSE ANSWER. `AskResponse` carries no `patient_id`, but it does carry
 * `patient_session_key` = `<user>:<patient>:<session>`. The module checks
 * that the patient and session in it are the ones it sent — the same check
 * AgentClient applies to Week 1 responses (F-62) — and then drops the key:
 * like the agent's session id, it never reaches the browser.
 *
 * `status` IS RENAMED to `answer_status`, for the reason ExtractionState
 * renames it: the module's envelope already uses `status`.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

use OpenEMR\Modules\ClinicalCopilot\ConversationSessionId;
use OpenEMR\Modules\ClinicalCopilot\OpenPatient;
use OpenEMR\Modules\ClinicalCopilot\PatientUuid;

final readonly class AskAnswer
{
    /** `AskStatus`. */
    public const STATUSES = ['answered', 'partially_answered', 'not_in_record', 'refused'];

    /** `AskRequest.document_ids` max_length (MAX_DOCUMENTS_PER_TURN). */
    public const MAX_DOCUMENTS = 4;

    /** What `renderAnswerView()` draws (public/assets/w2/answer-view.mjs). */
    public const ANSWER_VIEW_SCHEMA = 'w2-ui/answer-view/1.0.0';

    /**
     * @param array<mixed>|null $answerView
     * @param list<array<mixed>> $unverified
     * @param list<array<mixed>> $criticalValues
     * @param list<array<mixed>> $discrepancies
     * @param list<string> $limitations
     * @param list<string> $gaps
     * @param array{turn: int, transition: string, context_turns: int} $conversation
     */
    private function __construct(
        public string $answerStatus,
        public string $reasonCode,
        public string $message,
        public ?array $answerView,
        public array $unverified,
        public array $criticalValues,
        public array $discrepancies,
        public array $limitations,
        public array $gaps,
        public array $conversation,
        public int $totalMs,
        public string $correlationId,
    ) {
    }

    /**
     * @param array<mixed> $body The decoded 200 response.
     * @throws \UnexpectedValueException If the body does not match the contract or is for anyone else.
     */
    public static function fromAgentResponse(array $body, PatientUuid $patient, ConversationSessionId $session): self
    {
        $key = explode(':', AgentJson::string($body, 'patient_session_key'));
        if (count($key) !== 3 || $key[1] !== $patient->value || $key[2] !== $session->value) {
            throw new \UnexpectedValueException('Agent answer is not for the patient and conversation it was asked about');
        }

        $answerView = $body['answer_view'] ?? null;
        if ($answerView !== null) {
            if (!is_array($answerView) || ($answerView['schema_version'] ?? null) !== self::ANSWER_VIEW_SCHEMA) {
                throw new \UnexpectedValueException('Agent answer_view is not a ' . self::ANSWER_VIEW_SCHEMA . ' object');
            }
        }

        $unverified = AgentJson::rows($body, 'unverified');
        foreach ($unverified as $row) {
            AgentJson::string($row, 'line');
        }
        $criticalValues = AgentJson::rows($body, 'critical_values');
        foreach ($criticalValues as $row) {
            AgentJson::string($row, 'line');
        }

        $conversation = AgentJson::map($body, 'conversation');
        $totalMs = $body['total_ms'] ?? 0;

        return new self(
            AgentJson::oneOf($body, 'status', self::STATUSES),
            AgentJson::code($body, 'reason_code'),
            AgentJson::string($body, 'message'),
            $answerView,
            $unverified,
            $criticalValues,
            AgentJson::rows($body, 'discrepancies'),
            AgentJson::codes($body, 'limitations'),
            AgentJson::codes($body, 'gaps'),
            [
                'turn' => AgentJson::int($conversation, 'turn'),
                'transition' => AgentJson::code($conversation, 'transition'),
                'context_turns' => AgentJson::int($conversation, 'context_turns'),
            ],
            is_int($totalMs) && $totalMs >= 0 ? $totalMs : 0,
            AgentJson::string($body, 'correlation_id'),
        );
    }

    /**
     * @param OpenPatient $patient Whom the answer is about: required, so no answer reaches the card unnamed (F-62).
     * @return array<string, mixed>
     */
    public function toCardPayload(OpenPatient $patient): array
    {
        return [
            'status' => 'ok',
            'patient' => $patient->toCardPayload(),
            'answer_status' => $this->answerStatus,
            'reason_code' => $this->reasonCode,
            'message' => $this->message,
            'answer_view' => $this->answerView,
            'unverified' => $this->unverified,
            'critical_values' => $this->criticalValues,
            'discrepancies' => $this->discrepancies,
            'limitations' => $this->limitations,
            'gaps' => $this->gaps,
            'conversation' => $this->conversation,
            'total_ms' => $this->totalMs,
            'correlation_id' => $this->correlationId,
        ];
    }
}
