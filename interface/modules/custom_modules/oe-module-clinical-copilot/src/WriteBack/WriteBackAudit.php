<?php

/**
 * The write-back's own audit entries.
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
 * Core audits the `procedure_report` and `procedure_result` inserts through
 * `EventAuditLogger::auditSQLEvent()`, but **not** the `procedure_order`
 * row: `LOG_TABLES` maps it to the `lab-order` event and
 * `audit_events_lab-order` is not a global `library/globals.inc.php`
 * defines, so `isEventTypeEnabled()` is false for it on every instance.
 * Verified on the local stack — writing a chain produces `lab-results-insert`
 * rows for the report and the result and nothing for the order.
 *
 * These entries fill that gap, and they are written from **outside** the
 * transaction on purpose. OpenEMR's audit inserts do not roll back with the
 * work they describe — which is right for an audit log — so an entry
 * emitted inside the transaction can end up describing rows that were
 * undone. That is not hypothetical: the first version of this did exactly
 * that, and the losing side of an idempotency race left an audit row
 * claiming an order id that no longer existed.
 */
interface WriteBackAudit
{
    /**
     * A committed write.
     *
     * @param list<int> $resultIds
     */
    public function orderWritten(int $pid, int $procedureOrderId, array $resultIds): void;

    /**
     * An attempt that wrote nothing — a rollback, or the losing side of an
     * idempotency race. Recorded because an attempted write into a chart is
     * an event whether or not it landed.
     */
    public function writeAbandoned(int $pid, string $reason): void;
}
