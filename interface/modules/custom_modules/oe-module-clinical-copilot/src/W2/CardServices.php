<?php

/**
 * The token broker and agent client the card's routes use. Every route builds
 * its broker here, brief.php and conversation.php included, so none can drift
 * from the others or be built without the token fetch lock.
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
use OpenEMR\Modules\ClinicalCopilot\DatabaseTokenFetchLock;
use OpenEMR\Modules\ClinicalCopilot\ModuleConfig;
use Psr\Log\LoggerInterface;
use Symfony\Component\HttpClient\HttpClient;

final class CardServices
{
    /**
     * @param mixed $siteId The session's site_id, as stored.
     * @param mixed $userId The session's authUserID, as stored. A session without one (never, past globals.php)
     *     shares a single lock with every other such session: waits longer, never races.
     */
    public static function broker(ModuleConfig $config, string $webRoot, mixed $siteId, mixed $userId, LoggerInterface $logger): ClinicianTokenBroker
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
            DatabaseTokenFetchLock::viaQueryUtils(),
            is_int($userId) || is_string($userId) ? (string) $userId : '',
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
