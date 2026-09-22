<?php

/**
 * A write-back request this endpoint will not act on.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use InvalidArgumentException;

/**
 * Carries the offending field so the caller is told what to fix, and
 * nothing else: the message is echoed back over HTTP, so it names fields
 * and states rules and never quotes a value out of the request body
 * (CLAUDE.md invariant 1 — a lab value is chart content).
 */
final class InvalidWriteBackRequest extends InvalidArgumentException
{
    public function __construct(public readonly string $field, string $reason)
    {
        parent::__construct($reason);
    }
}
