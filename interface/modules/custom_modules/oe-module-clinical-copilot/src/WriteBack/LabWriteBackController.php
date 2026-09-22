<?php

/**
 * HTTP boundary for the lab write-back endpoint.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\WriteBack;

use OpenEMR\Common\Http\HttpRestRequest;
use OpenEMR\RestControllers\Config\RestConfig;
use Psr\Log\LoggerInterface;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Response;

/**
 * Parses the request into typed objects, checks the two ACLs the write
 * actually needs, and turns the service's result — or its refusal — into
 * a response.
 *
 * The OAuth scope is already enforced before this runs:
 * `HttpRestRouteHandler::checkSecurity()` derives `user/lab-writeback.c`
 * from the route and the method and hands it to `AuthorizationListener`.
 * The ACL checks here are the second half of the same question, and they
 * are not redundant with it — a scope says what the *client application*
 * may ask for, an ACL says what this *clinician* may do. Both are required
 * to write into a chart, and they are the same two ACLs OpenEMR's own
 * screens use: `patients`/`docs` for the document upload
 * (`_rest_routes_standard.inc.php:496`) and `patients`/`lab` for lab
 * results (`interface/patient_file/summary/labdata.php:49`).
 */
final readonly class LabWriteBackController
{
    /** Bytes. A lab PDF, base64-encoded, plus one result's worth of JSON. */
    private const MAX_BODY_BYTES = 33_554_432;

    public function __construct(
        private LabWriteBackService $service,
        private LoggerInterface $logger,
    ) {
    }

    public function post(HttpRestRequest $request): Response
    {
        RestConfig::request_authorization_check($request, 'patients', 'docs', ['write', 'addonly']);
        RestConfig::request_authorization_check($request, 'patients', 'lab', ['write', 'addonly']);

        $userId = $request->getRequestUserId();
        if ($userId === null || $userId < 1) {
            // Every write is attributed to the clinician whose token this
            // is; there is no fallback to a service account.
            return $this->problem(Response::HTTP_UNAUTHORIZED, null, 'the request has no OpenEMR user');
        }

        try {
            $parsed = LabWriteBackRequest::fromArray($this->decodeBody($request));
            $outcome = $this->service->write($parsed, $userId);
        } catch (InvalidWriteBackRequest $exception) {
            return $this->problem(Response::HTTP_BAD_REQUEST, $exception->field, $exception->getMessage());
        } catch (WriteBackFailed $exception) {
            if ($exception->statusCode >= Response::HTTP_INTERNAL_SERVER_ERROR) {
                $this->logger->error('Clinical Co-Pilot lab write-back could not be completed', [
                    'exception' => $exception,
                ]);
            }
            return $this->problem($exception->statusCode, null, $exception->getMessage());
        }

        return new JsonResponse($outcome->toArray(), Response::HTTP_OK);
    }

    /**
     * @return array<string, mixed>
     * @throws InvalidWriteBackRequest
     */
    private function decodeBody(HttpRestRequest $request): array
    {
        $body = $request->getContent();
        if ($body === '') {
            throw new InvalidWriteBackRequest('request', 'has a JSON body');
        }
        if (strlen($body) > self::MAX_BODY_BYTES) {
            throw new InvalidWriteBackRequest(
                'request',
                'is at most ' . self::MAX_BODY_BYTES . ' bytes'
            );
        }
        try {
            $decoded = json_decode($body, true, 32, JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            // The parser's message can quote the body, which is chart
            // content. The caller is told the shape, not their own bytes.
            throw new InvalidWriteBackRequest('request', 'is valid JSON');
        }
        if (!is_array($decoded) || array_is_list($decoded)) {
            throw new InvalidWriteBackRequest('request', 'is a JSON object');
        }
        /** @var array<string, mixed> $decoded */
        return $decoded;
    }

    private function problem(int $status, ?string $field, string $message): JsonResponse
    {
        $body = ['error' => 'lab_writeback_refused', 'message' => $message];
        if ($field !== null) {
            $body['field'] = $field;
        }
        return new JsonResponse($body, $status);
    }
}
