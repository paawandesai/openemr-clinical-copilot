<?php

/**
 * Why a Pre-Visit Brief could not be shown. The value is the error code the
 * card receives; the message is what the physician reads.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

enum BriefFailure: string
{
    case InvalidRequest = 'invalid_request';
    case Forbidden = 'forbidden';
    case PatientContextChanged = 'patient_context_changed';
    case NotConfigured = 'not_configured';
    case TokenUnavailable = 'token_unavailable';
    case AgentRejectedToken = 'agent_rejected_token';
    case PatientNotFound = 'patient_not_found';
    case AgentUnavailable = 'agent_unavailable';
    case BriefGenerationFailed = 'brief_generation_failed';

    public function httpStatus(): int
    {
        return match ($this) {
            self::InvalidRequest => 400,
            self::Forbidden => 403,
            self::PatientNotFound => 404,
            self::PatientContextChanged => 409,
            self::TokenUnavailable,
            self::AgentRejectedToken,
            self::AgentUnavailable,
            self::BriefGenerationFailed => 502,
            self::NotConfigured => 503,
        };
    }

    /**
     * @return literal-string Untranslated; the caller passes it through xl(),
     *     which turns apostrophes into backticks, so none are used.
     */
    public function message(): string
    {
        return match ($this) {
            self::InvalidRequest => 'The brief request was malformed. Reload the chart and try again.',
            self::Forbidden => 'You do not have access to the medical record of this patient.',
            self::PatientContextChanged => 'A different patient is now open in this session. Reload this chart to generate a brief for the patient shown.',
            self::NotConfigured => 'The Clinical Co-Pilot is not configured. Ask an administrator to complete Administration > Config > Clinical Co-Pilot.',
            self::TokenUnavailable => 'OpenEMR did not issue an access token for your session. Reload the chart; if this continues, ask an administrator to check the Clinical Co-Pilot API client.',
            self::AgentRejectedToken => 'The co-pilot could not read this chart with your access. Ask an administrator to check the Clinical Co-Pilot API client scopes.',
            self::PatientNotFound => 'The co-pilot could not find this patient in the OpenEMR FHIR API.',
            self::AgentUnavailable => 'The co-pilot service is unavailable right now. The chart itself is unaffected.',
            self::BriefGenerationFailed => 'The co-pilot could not generate a brief for this patient. No partial brief is shown.',
        };
    }
}
