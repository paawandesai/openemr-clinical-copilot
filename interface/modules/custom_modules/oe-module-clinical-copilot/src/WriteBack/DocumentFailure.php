<?php

/**
 * Why a source-document read was refused.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

/**
 * Every message here is fixed text about the request, never about the
 * document: "not found" is the same answer whether the id does not exist,
 * belongs to another patient, or has been deleted, so the endpoint cannot
 * be used to learn which (CLAUDE.md invariant 1 — and a document id that
 * exists for a patient is itself a fact about that patient).
 */
enum DocumentFailure: string
{
    case InvalidRequest = 'invalid_request';
    case Forbidden = 'forbidden';
    case NoOpenChart = 'no_open_chart';
    case NotFound = 'not_found';
    case NotPreviewable = 'not_previewable';
    case Unreadable = 'unreadable';

    public function httpStatus(): int
    {
        return match ($this) {
            self::InvalidRequest => 400,
            self::Forbidden, self::NoOpenChart => 403,
            self::NotFound => 404,
            self::NotPreviewable => 415,
            self::Unreadable => 500,
        };
    }

    /**
     * @return literal-string `xl()` translates literals only, and these are
     *                        the literals it is given.
     */
    public function message(): string
    {
        return match ($this) {
            self::InvalidRequest => 'This request could not be read.',
            self::Forbidden => 'You do not have permission to view patient documents.',
            self::NoOpenChart => 'Open a patient chart before viewing a document.',
            self::NotFound => 'That document is not available for the open chart.',
            self::NotPreviewable => 'Only PDF documents can be previewed here.',
            self::Unreadable => 'That document could not be read.',
        };
    }
}
