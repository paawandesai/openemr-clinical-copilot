<?php

/**
 * Clinical Co-Pilot module bootstrap. Included by
 * ModulesApplication::loadCustomModule() once the module is enabled.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Core\OEGlobalsBag;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

/**
 * @var ModulesClassLoader $classLoader Injected by the module loader.
 * @var EventDispatcherInterface $eventDispatcher Injected by the module loader.
 */
$classLoader->registerNamespaceIfNotExists('OpenEMR\\Modules\\ClinicalCopilot\\', __DIR__ . DIRECTORY_SEPARATOR . 'src');

(new Bootstrap(
    $eventDispatcher,
    OEGlobalsBag::getInstance(),
    ServiceContainer::getCrypto(),
    ServiceContainer::getLogger(),
))->subscribeToEvents();
