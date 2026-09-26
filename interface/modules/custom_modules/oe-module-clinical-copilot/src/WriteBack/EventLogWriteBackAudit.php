<?php

/**
 * The production WriteBackAudit: OpenEMR's own audit log.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use OpenEMR\Common\Logging\EventAuditLogger;
use Symfony\Component\HttpFoundation\Session\SessionInterface;

/**
 * `newEvent()` rather than letting `auditSQLEvent()` pick the event up: the
 * explicit call is not gated by the `audit_events_*` flags, and it can name
 * the patient, which `auditSQLEvent()` cannot for an API request (it reads
 * the pid from a session that a bearer request does not have).
 *
 * Identifiers and counts only — no test code, no value, no unit. The
 * `procedure_result` rows these entries point at are themselves audited by
 * core with their full statement, so nothing is lost by keeping clinical
 * content out of here (CLAUDE.md invariant 1).
 */
final readonly class EventLogWriteBackAudit implements WriteBackAudit
{
    private const EVENT = 'lab-results';

    public function __construct(private SessionInterface $session)
    {
    }

    /** @param list<int> $resultIds */
    public function orderWritten(int $pid, int $procedureOrderId, array $resultIds): void
    {
        $this->record(
            true,
            $pid,
            'procedure_order ' . $procedureOrderId . ' committed with ' . count($resultIds)
            . ' result(s) [' . implode(',', $resultIds) . ']. Core does not audit '
            . 'procedure_order: audit_events_lab-order is not a configurable global.'
        );
    }

    public function writeAbandoned(int $pid, string $reason): void
    {
        $this->record(false, $pid, 'write-back attempt wrote nothing: ' . $reason);
    }

    /** @param list<int> $chartResultIds */
    public function conflictsFound(int $pid, array $chartResultIds): void
    {
        // A success: the endpoint did what it should, which was to refuse
        // to overwrite. Ids only; the values are on the rows themselves.
        $this->record(
            true,
            $pid,
            count($chartResultIds) . ' result(s) in the document disagree with procedure_result ['
            . implode(',', $chartResultIds) . '] already in the chart; nothing written or overwritten'
        );
    }

    private function record(bool $success, int $pid, string $comment): void
    {
        EventAuditLogger::getInstance()->newEvent(
            self::EVENT,
            self::sessionString($this->session->get('authUser')),
            self::sessionString($this->session->get('authProvider')),
            $success ? 1 : 0,
            'Clinical Co-Pilot write-back: ' . $comment,
            $pid,
        );
    }

    /** Session values are untyped; an audit row records a name or nothing. */
    private static function sessionString(mixed $value): string
    {
        return is_string($value) ? $value : '';
    }
}
