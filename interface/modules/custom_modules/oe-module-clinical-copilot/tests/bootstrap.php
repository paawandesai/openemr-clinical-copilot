<?php

/**
 * PHPUnit bootstrap for the Clinical Co-Pilot module's isolated tests: the
 * Composer autoloader and the module namespace, no database or globals.php.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

if (php_sapi_name() !== 'cli') {
    exit;
}

$autoloader = require dirname(__DIR__, 5) . '/vendor/autoload.php';
if (!$autoloader instanceof \Composer\Autoload\ClassLoader) {
    throw new \RuntimeException('vendor/autoload.php did not return the Composer class loader');
}
$autoloader->addPsr4('OpenEMR\\Modules\\ClinicalCopilot\\', dirname(__DIR__) . '/src');
$autoloader->addPsr4('OpenEMR\\Modules\\ClinicalCopilot\\Tests\\', __DIR__);
