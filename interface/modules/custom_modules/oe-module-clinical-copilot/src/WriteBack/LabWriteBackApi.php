<?php

/**
 * Registers the lab write-back route and its custom OAuth scope.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Http\HttpRestRequest;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\RestApiExtend\RestApiCreateEvent;
use OpenEMR\Events\RestApiExtend\RestApiScopeEvent;
use OpenEMR\Services\DocumentService;
use Psr\Log\LoggerInterface;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;
use Symfony\Component\HttpFoundation\Response;

/**
 * D1 calls for "a custom endpoint in the OpenEMR fork, protected by a new
 * custom OAuth scope". Both halves are module extension points that
 * already exist, which is why this ships as a module rather than as edits
 * to `apis/routes/` and `ServerScopeListEntity`:
 *
 * - `RestApiCreateEvent` (`restConfig.route_map.create`) is dispatched by
 *   `StandardRouteFinder::find()` on every standard-API request, after the
 *   core route map is loaded, and the added route is then dispatched,
 *   scope-checked and audited exactly like a core one.
 * - `RestApiScopeEvent` (`api.scope.get-supported-scopes`) is dispatched by
 *   `ScopeRepository::getCurrentSmartScopes()`, which is what dynamic
 *   client registration validates a requested scope against and what the
 *   token endpoint grants from.
 *
 * Both fire because `interface/globals.php` — which constructs
 * `ModulesApplication` and so runs this module's bootstrap — is loaded by
 * `SiteSetupListener` for `/apis/` and `/oauth2/` alike.
 *
 * ### The scope
 *
 * `user/lab-writeback.c`. The resource half is not free choice:
 * `HttpRestParsedRoute` takes the last non-parameter path segment of the
 * route as the resource, and `HttpRestRouteHandler::checkSecurity()` maps
 * POST to the `c` permission, so the route path and the scope string are
 * two spellings of one thing. `user/` and not `patient/`: this is a
 * clinician-initiated write, and `AuthorizationListener` refuses a
 * patient-role token on the standard API anyway.
 *
 * `.c` rather than `.write`: create is the only thing this endpoint does.
 * There is no update and no delete here — a correction is a new document
 * and a new extraction, not an edit of a value already in the chart — so
 * granting `cud` would hand out two permissions nothing implements.
 */
final readonly class LabWriteBackApi
{
    public const ROUTE = 'POST /api/clinical-copilot/lab-writeback';

    /** The resource half of the scope; also the route's last path segment. */
    public const SCOPE_RESOURCE = 'lab-writeback';

    public const SCOPE = 'user/' . self::SCOPE_RESOURCE . '.c';

    public function __construct(
        private OEGlobalsBag $globals,
        private LoggerInterface $logger,
    ) {
    }

    public function subscribe(EventDispatcherInterface $dispatcher): void
    {
        $dispatcher->addListener(RestApiCreateEvent::EVENT_HANDLE, $this->addRoute(...));
        $dispatcher->addListener(
            RestApiScopeEvent::EVENT_TYPE_GET_SUPPORTED_SCOPES,
            $this->addScope(...)
        );
    }

    public function addRoute(RestApiCreateEvent $event): void
    {
        $event->addToRouteMap(
            self::ROUTE,
            fn(HttpRestRequest $request): Response => $this->controller()->post($request)
        );
    }

    /**
     * The event is dispatched once per API type with the list accumulated
     * so far, so this adds the scope on the standard-API pass only —
     * adding it on both would put the same string in the list twice.
     */
    public function addScope(RestApiScopeEvent $event): void
    {
        if ($event->getApiType() !== RestApiScopeEvent::API_TYPE_STANDARD) {
            return;
        }
        $scopes = $event->getScopes();
        if (!in_array(self::SCOPE, $scopes, true)) {
            $scopes[] = self::SCOPE;
            $event->setScopes($scopes);
        }
    }

    /**
     * The composition root for this endpoint. Built per request rather
     * than at bootstrap: the module's bootstrap runs on every OpenEMR page
     * load, and it has to stay cheap.
     */
    private function controller(): LabWriteBackController
    {
        return new LabWriteBackController(
            new LabWriteBackService(
                new PatientDirectory(),
                new DocumentStore(new DocumentService(), $this->globals),
                new ProcedureChainWriter(),
                new WriteBackLedger(ServiceContainer::getClock()),
                $this->logger,
            ),
            $this->logger,
        );
    }
}
