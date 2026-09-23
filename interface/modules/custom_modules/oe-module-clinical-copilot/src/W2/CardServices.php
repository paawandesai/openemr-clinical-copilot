<?php

/**
 * The token broker and agent client the Week 2 card's routes use, built the
 * way brief.php and conversation.php build theirs, so the three new routes
 * cannot drift from them or from each other.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\W2;

use OpenEMR\FHIR\Config\ServerConfig;
use OpenEMR\Modules\ClinicalCopilot\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\ClinicianTokenBroker;
use OpenEMR\Modules\ClinicalCopilot\CorrelationId;
use OpenEMR\Modules\ClinicalCopilot\ModuleConfig;
use Psr\Log\LoggerInterface;
use Symfony\Component\HttpClient\HttpClient;

final class CardServices
{
    /**
     * @param mixed $siteId The session's site_id, as stored.
     */
    public static function broker(ModuleConfig $config, string $webRoot, mixed $siteId, LoggerInterface $logger): ClinicianTokenBroker
    {
        return new ClinicianTokenBroker(
            HttpClient::create(),
            $logger,
            ClinicianTokenBroker::smartLaunchToken(...),
            $config->loopbackUrl . $webRoot . '/oauth2/' . rawurlencode(is_string($siteId) && $siteId !== '' ? $siteId : 'default'),
            (new ServerConfig())->getFhirUrl(),
            $config->clientId,
            $config->clientSecret,
            $config->redirectUri,
        );
    }

    public static function agent(ModuleConfig $config, LoggerInterface $logger, CorrelationId $correlationId): AgentClient
    {
        return new AgentClient(
            HttpClient::create($config->agentCaFile === null ? [] : ['cafile' => $config->agentCaFile]),
            $logger,
            $config->agentUrl,
            $correlationId,
        );
    }
}
