<?php

/**
 * The parts of the agent's PreVisitBriefResponse (agent/app/models/api.py)
 * the chart card shows.
 *
 * The brief text is shown as the agent rendered it. The agent's renderer
 * decides which items are UNVERIFIED and which sections are "not recorded" or
 * "none active"; re-deriving that here from the claim list would be a second
 * implementation free to drift from the first.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

final readonly class PreVisitBrief
{
    private function __construct(
        public string $text,
        public int $claims,
        public int $verified,
        public int $unverified,
        public string $correlationId,
    ) {
    }

    /**
     * @param array<mixed> $body The decoded 200 response.
     * @throws \UnexpectedValueException If the body does not match the contract.
     */
    public static function fromAgentResponse(array $body): self
    {
        $text = $body['brief'] ?? null;
        $correlationId = $body['correlation_id'] ?? null;
        $verification = $body['verification'] ?? null;
        if (!is_string($text) || $text === '' || !is_string($correlationId) || !is_array($verification)) {
            throw new \UnexpectedValueException('Agent response does not match the PreVisitBriefResponse contract');
        }
        $claims = $verification['claims'] ?? null;
        $verified = $verification['verified'] ?? null;
        $unverified = $verification['unverified'] ?? null;
        if (
            !is_int($claims) || !is_int($verified) || !is_int($unverified)
            || $verified < 0 || $unverified < 0 || $verified + $unverified !== $claims
        ) {
            throw new \UnexpectedValueException('Agent verification summary is inconsistent');
        }
        return new self($text, $claims, $verified, $unverified, $correlationId);
    }

    /**
     * @param OpenPatient $patient Whom the brief is about: required, so no brief reaches the card unnamed (F-62).
     * @return array{status: 'ok', patient: array{name: string, date_of_birth: ?string}, brief: string, verification: array{claims: int, verified: int, unverified: int}, correlation_id: string}
     */
    public function toCardPayload(OpenPatient $patient): array
    {
        return [
            'status' => 'ok',
            'patient' => $patient->toCardPayload(),
            'brief' => $this->text,
            'verification' => [
                'claims' => $this->claims,
                'verified' => $this->verified,
                'unverified' => $this->unverified,
            ],
            'correlation_id' => $this->correlationId,
        ];
    }
}
