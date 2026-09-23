<?php

/**
 * A decrypted source document, ready to serve.
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
 * `bytes` is chart content — a lab report — so this type exists partly to
 * make that obvious at every call site. It is written to the response body
 * and never to a log, a message or an exception.
 */
final readonly class SourceDocumentBytes
{
    public function __construct(
        public int $documentId,
        public string $bytes,
        public string $mimeType,
        public string $filename,
    ) {
    }
}
