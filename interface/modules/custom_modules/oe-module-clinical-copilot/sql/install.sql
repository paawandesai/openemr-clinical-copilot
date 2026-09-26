--
-- Clinical Co-Pilot module install SQL.
--
-- Run by Installer\Model\InstModuleTable::installSQL() through
-- OpenEMR\Services\Utils\SQLUpgradeService, which is why the #IfNotTable /
-- #EndIf directives below are honoured. From a shell:
--
--   php bin/console openemr:zfc-module \
--       --modname=oe-module-clinical-copilot --modaction=install_sql
--

#IfNotTable module_clinical_copilot_writeback_ledger
CREATE TABLE `module_clinical_copilot_writeback_ledger` (
  `id`                  BIGINT(20)   NOT NULL AUTO_INCREMENT,
  `idempotency_key`     VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
                        COMMENT 'D1 UNIQUE key: <document sha3-512>:<TEST CODE>:<YYYY-MM-DD>',
  `extraction_id`       VARCHAR(64)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL
                        COMMENT 'w2x-<32 hex>; the only thing written to procedure_result.comments',
  `document_sha3_512`   CHAR(128)    CHARACTER SET ascii COLLATE ascii_bin NOT NULL
                        COMMENT 'documents.hash of the source file; OpenEMR hashes with sha3-512',
  `document_id`         BIGINT(20)   NOT NULL COMMENT 'references documents.id',
  `patient_uuid`        CHAR(36)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL
                        COMMENT 'patient_data.uuid as a FHIR id string, never a pid',
  `test_code`           VARCHAR(64)  NOT NULL COMMENT 'LOINC code; part of the idempotency key',
  `collection_date`     DATE         NOT NULL COMMENT 'part of the idempotency key',
  `page`                INT(11)      NOT NULL COMMENT '1-based page of the source document',
  `bbox_x0`             DOUBLE       NOT NULL COMMENT 'PDF points, origin top-left, y down',
  `bbox_y0`             DOUBLE       NOT NULL,
  `bbox_x1`             DOUBLE       NOT NULL,
  `bbox_y1`             DOUBLE       NOT NULL,
  `procedure_order_id`  BIGINT(20)   NOT NULL COMMENT 'references procedure_order.procedure_order_id',
  `procedure_report_id` BIGINT(20)   NOT NULL COMMENT 'references procedure_report.procedure_report_id',
  `procedure_result_id` BIGINT(20)   NOT NULL COMMENT 'references procedure_result.procedure_result_id',
  `written_at`          DATETIME     NOT NULL,
  `extractor_version`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT 'empty until the request carries it',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idempotency_key` (`idempotency_key`),
  KEY `extraction_id` (`extraction_id`),
  KEY `document_sha3_512` (`document_sha3_512`),
  KEY `patient_uuid` (`patient_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='Clinical Co-Pilot lab write-back ledger: idempotency key -> the ids it wrote, and the page region the value came from';
#EndIf

-- D60: where an upload whose printed name or date of birth contradicts the
-- chart is moved (W2\ChartDocuments::quarantine). A top-level document
-- category with the stock ACL, so anyone who can see the chart's documents
-- sees it. Inserted as the last child of the root, the way OpenEMR's own
-- upgrade scripts add categories (sql/4_0_0-to-4_1_0_upgrade.sql, "CCR"):
-- the new node takes the root's right edge, and the root widens by two.
-- Idempotent: the directive skips it once the row exists.
#IfNotRow categories name Quarantine - Patient Mismatch
INSERT INTO categories SELECT (SELECT MAX(id) FROM categories) + 1, 'Quarantine - Patient Mismatch', '', 1, rght, rght + 1, 'patients|docs', '' FROM categories WHERE name = 'Categories';
UPDATE categories SET rght = rght + 2 WHERE name = 'Categories';
UPDATE categories_seq SET id = (SELECT MAX(id) FROM categories);
#EndIf
