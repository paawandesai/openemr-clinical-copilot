<?php

/**
 * Narrowing helpers for requests captured from Symfony's MockHttpClient.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Tests;

final readonly class HttpExchange
{
    /**
     * @param array<mixed> $options
     */
    public function __construct(
        public string $method,
        public string $url,
        private array $options,
    ) {
    }

    /**
     * @return array<string, string>
     */
    public function query(): array
    {
        return self::stringPairs((string) parse_url($this->url, PHP_URL_QUERY));
    }

    /**
     * @return list<string> "Name: value" lines, as the client sends them.
     */
    public function headerLines(): array
    {
        $headers = $this->options['headers'] ?? [];
        return is_array($headers) ? array_values(array_filter($headers, is_string(...))) : [];
    }

    public function body(): string
    {
        $body = $this->options['body'] ?? '';
        return is_string($body) ? $body : '';
    }

    /**
     * @return array<string, string>
     */
    public function formBody(): array
    {
        return self::stringPairs($this->body());
    }

    /**
     * @return array<string, string>
     */
    public static function stringPairs(string $encoded): array
    {
        parse_str($encoded, $pairs);
        $strings = [];
        foreach ($pairs as $key => $value) {
            if (is_string($value)) {
                $strings[(string) $key] = $value;
            }
        }
        return $strings;
    }
}
