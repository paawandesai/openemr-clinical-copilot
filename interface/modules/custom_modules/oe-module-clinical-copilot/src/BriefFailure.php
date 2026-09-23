<?php

/**
 * Why a Pre-Visit Brief, an answer in its follow-up conversation, or a Week 2
 * upload, extraction status or answer (the Documents & Sources card) could
 * not be shown. The value is the error code the card receives; the message is
 * what the physician reads.
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
    case InvalidQuestion = 'invalid_question';
    case AnswerGenerationFailed = 'answer_generation_failed';
    // Week 2 (docs/w2/ui-integration.md §2.2-2.3). 413 for an oversize
    // upload is also `upload_rejected`; upload.php sets that status itself.
    case UploadRejected = 'upload_rejected';
    case DocumentAttachedElsewhere = 'document_attached_elsewhere';
    case ExtractionNotFound = 'extraction_not_found';
    case RateLimited = 'rate_limited';
    case ExtractionBusy = 'extraction_capacity';
    case DocumentNotStored = 'document_not_stored';

    public function httpStatus(): int
    {
        return match ($this) {
            self::InvalidRequest,
            self::InvalidQuestion,
            self::UploadRejected => 400,
            self::Forbidden => 403,
            self::PatientNotFound,
            self::ExtractionNotFound => 404,
            self::PatientContextChanged,
            self::DocumentAttachedElsewhere => 409,
            self::RateLimited => 429,
            self::DocumentNotStored => 500,
            self::TokenUnavailable,
            self::AgentRejectedToken,
            self::AgentUnavailable,
            self::BriefGenerationFailed,
            self::AnswerGenerationFailed => 502,
            self::NotConfigured,
            self::ExtractionBusy => 503,
        };
    }

    /**
     * @return literal-string Untranslated; the caller passes it through xl(),
     *     which turns apostrophes into backticks, so none are used.
     */
    public function message(): string
    {
        return match ($this) {
            self::InvalidRequest => 'The request was malformed. Reload the chart and try again.',
            self::Forbidden => 'You do not have access to the medical record of this patient.',
            self::PatientContextChanged => 'A different patient is now open in this session. Reload this chart to use the co-pilot for the patient shown.',
            self::NotConfigured => 'The Clinical Co-Pilot is not configured. Ask an administrator to complete Administration > Config > Clinical Co-Pilot.',
            self::TokenUnavailable => 'OpenEMR did not issue an access token for your session. Reload the chart; if this continues, ask an administrator to check the Clinical Co-Pilot API client.',
            self::AgentRejectedToken => 'The co-pilot could not read this chart with your access. Ask an administrator to check the Clinical Co-Pilot API client scopes.',
            self::PatientNotFound => 'The co-pilot could not find this patient in the OpenEMR FHIR API.',
            self::AgentUnavailable => 'The co-pilot service is unavailable right now. The chart itself is unaffected.',
            self::BriefGenerationFailed => 'The co-pilot could not generate a brief for this patient. No partial brief is shown.',
            self::InvalidQuestion => 'Enter a question of up to 500 characters.',
            self::AnswerGenerationFailed => 'The co-pilot could not answer that question. No partial answer is shown, and nothing was added to the conversation.',
            self::UploadRejected => 'The upload was not accepted. Upload a PDF of up to 25 MB, as a lab report or an intake form.',
            // D40 item 8. Says that the file is on some other chart, never which.
            self::DocumentAttachedElsewhere => 'This file is already attached to a different chart, so it cannot be attached to this one.',
            self::ExtractionNotFound => 'No extraction is recorded for that document on this chart.',
            self::RateLimited => 'Too many documents were uploaded in a short time. Wait a few minutes and try again.',
            self::ExtractionBusy => 'The co-pilot is extracting other documents right now. Try again in a moment.',
            self::DocumentNotStored => 'The document was read but could not be saved to this chart. Nothing was attached; try the upload again.',
        };
    }
}
