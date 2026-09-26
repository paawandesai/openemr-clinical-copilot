<?php

/**
 * Wires the Clinical Co-Pilot into OpenEMR: its settings section, its Twig
 * templates, the Pre-Visit Brief card with its follow-up conversation at the
 * top of the patient dashboard's main column, the Week 2 Documents & Sources
 * card under it (upload, sourced answers, the cited PDF page), and the lab
 * write-back REST endpoint with its custom OAuth scope.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Crypto\CryptoInterface;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\Core\TwigEnvironmentEvent;
use OpenEMR\Events\Globals\GlobalsInitializedEvent;
use OpenEMR\Events\Patient\Summary\Card\CardModel;
use OpenEMR\Events\Patient\Summary\Card\SectionEvent;
use OpenEMR\Modules\ClinicalCopilot\W2\AskAnswer;
use OpenEMR\Modules\ClinicalCopilot\WriteBack\LabWriteBackApi;
use OpenEMR\Services\Globals\GlobalSetting;
use OpenEMR\Services\Globals\UserSettingsService;
use Psr\Log\LoggerInterface;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;
use Twig\Loader\FilesystemLoader;

final readonly class Bootstrap
{
    /**
     * Card element id, and the user_settings label its collapsed state is
     * saved under: library/ajax/user_settings.php only saves labels ending in
     * _expand.
     */
    public const CARD_ID = 'clinical_copilot_brief_ps_expand';

    /** Reading the brief is reading the medical record. */
    public const ACL = ['patients', 'med'];

    /** The Documents & Sources card; saved collapsed state as for CARD_ID. */
    public const SOURCES_CARD_ID = 'clinical_copilot_sources_ps_expand';

    /** Reading an uploaded document is reading the record's documents. */
    public const DOCUMENTS_ACL = ['patients', 'docs'];

    private const MODULE_PATH = '/interface/modules/custom_modules/oe-module-clinical-copilot';
    private const TEMPLATE_NAMESPACE = 'clinical_copilot';

    public function __construct(
        private EventDispatcherInterface $eventDispatcher,
        private OEGlobalsBag $globals,
        private CryptoInterface $crypto,
        private LoggerInterface $logger,
    ) {
    }

    public function subscribeToEvents(): void
    {
        $this->eventDispatcher->addListener(GlobalsInitializedEvent::EVENT_HANDLE, $this->addSettingsSection(...));
        $this->eventDispatcher->addListener(TwigEnvironmentEvent::EVENT_CREATED, $this->addTemplateNamespace(...));
        $this->eventDispatcher->addListener(SectionEvent::EVENT_HANDLE, $this->addBriefCard(...));
        $this->eventDispatcher->addListener(SectionEvent::EVENT_HANDLE, $this->addSourcesCard(...));
        // The write-back endpoint and the scope that protects it. Both are
        // registered unconditionally: the route has to exist for an
        // unauthorized call to get a 401 rather than a 404, and the scope has
        // to exist on the /oauth2/ request that registers a client, which
        // never touches the route.
        (new LabWriteBackApi($this->globals, $this->logger))->subscribe($this->eventDispatcher);
    }

    public function addSettingsSection(GlobalsInitializedEvent $event): void
    {
        $service = $event->getGlobalsService();
        $section = xl('Clinical Co-Pilot');
        $service->createSection($section);
        foreach (ModuleConfig::settingDefinitions() as $key => $setting) {
            $service->appendToSection(
                $section,
                $key,
                new GlobalSetting(xl($setting['title']), $setting['type'], $setting['default'], xl($setting['description'])),
            );
        }
    }

    /**
     * A namespace (@clinical_copilot/...) rather than a prepended path, so no
     * core template can be shadowed by this module.
     */
    public function addTemplateNamespace(TwigEnvironmentEvent $event): void
    {
        $loader = $event->getTwigEnvironment()->getLoader();
        $path = dirname(__DIR__) . '/templates';
        if ($loader instanceof FilesystemLoader && is_dir($path)) {
            $loader->addPath($path, self::TEMPLATE_NAMESPACE);
        }
    }

    /**
     * Runs while the patient dashboard renders. A runtime failure (a database
     * error, a duplicate card id) costs the card, never the chart: this is the
     * module's fault-isolation boundary, so it logs the exception and adds
     * nothing. \Error and \ErrorException are programming bugs and are left
     * to surface, per the project's ForbiddenCatchTypeRule.
     */
    public function addBriefCard(SectionEvent $event): void
    {
        if ($event->getSection() !== 'primary') {
            return;
        }
        try {
            $card = $this->buildCard();
            if ($card !== null) {
                $event->addCard($card);
            }
        } catch (\RuntimeException | \LogicException $e) {
            $this->logger->error('Clinical Co-Pilot card was not added to the patient dashboard', ['exception' => $e]);
        }
    }

    /**
     * The Week 2 card, under the brief. Same fault-isolation boundary as
     * addBriefCard(): a runtime failure costs this card, never the chart.
     */
    public function addSourcesCard(SectionEvent $event): void
    {
        if ($event->getSection() !== 'primary') {
            return;
        }
        try {
            $card = $this->buildSourcesCard();
            if ($card !== null) {
                $event->addCard($card);
            }
        } catch (\RuntimeException | \LogicException $e) {
            $this->logger->error('Clinical Co-Pilot sources card was not added to the patient dashboard', ['exception' => $e]);
        }
    }

    private function buildCard(): ?CardModel
    {
        if (!AclMain::aclCheckCore(self::ACL[0], self::ACL[1])) {
            return null;
        }
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $pid = filter_var($session->get('pid'), FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
        if ($pid === false) {
            return null;
        }
        try {
            ModuleConfig::fromGlobals($this->globals, $this->crypto);
        } catch (ModuleNotConfigured $e) {
            $this->logger->warning('Clinical Co-Pilot is enabled but not configured; card not shown', [
                'setting' => $e->setting,
            ]);
            return null;
        }

        $moduleUrl = $this->globals->getWebRoot() . self::MODULE_PATH;
        return new CardModel([
            'dispatcher' => $this->eventDispatcher,
            'title' => xl('Clinical Co-Pilot: Pre-Visit Brief'),
            'identifier' => self::CARD_ID,
            'acl' => self::ACL,
            'initiallyCollapsed' => in_array(UserSettingsService::getUserSetting(self::CARD_ID), ['0', 0], true),
            'add' => false,
            'edit' => false,
            'collapse' => true,
            'templateFile' => '@' . self::TEMPLATE_NAMESPACE . '/pre_visit_brief_card.html.twig',
            'templateVariables' => [
                'auth' => false,
                'pid' => $pid,
                'endpoint' => $moduleUrl . '/public/brief.php',
                'conversationEndpoint' => $moduleUrl . '/public/conversation.php',
                'questionMaxLength' => FollowUpQuestion::MAX_LENGTH,
                'scriptUrl' => $moduleUrl . '/public/assets/pre-visit-brief-card.js?v=' . $this->assetVersion(),
                'csrfToken' => CsrfUtils::collectCsrfToken($session),
            ],
        ]);
    }

    /**
     * The registration docs/w2/ui-integration.md §3 handed over, plus what the
     * ask path needs: the question endpoint and its bounds, and the
     * correlation id for this chart open.
     *
     * CORRELATION. One id is minted here, per render of the chart, and the
     * card sends it back on every upload, status poll and question; each
     * route forwards it as X-Correlation-ID, so the agent logs the whole
     * chart open as one encounter. It is a random UUID and names nothing.
     */
    private function buildSourcesCard(): ?CardModel
    {
        if (!AclMain::aclCheckCore(self::DOCUMENTS_ACL[0], self::DOCUMENTS_ACL[1])) {
            return null;
        }
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $pid = filter_var($session->get('pid'), FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
        if ($pid === false) {
            return null;
        }
        try {
            ModuleConfig::fromGlobals($this->globals, $this->crypto);
        } catch (ModuleNotConfigured $e) {
            $this->logger->warning('Clinical Co-Pilot sources card not shown', ['setting' => $e->setting]);
            return null;
        }

        $moduleUrl = $this->globals->getWebRoot() . self::MODULE_PATH;
        $version = $this->assetVersion();
        return new CardModel([
            'dispatcher' => $this->eventDispatcher,
            'title' => xl('Clinical Co-Pilot: Documents & Sources'),
            'identifier' => self::SOURCES_CARD_ID,
            'acl' => self::DOCUMENTS_ACL,
            'initiallyCollapsed' => in_array(UserSettingsService::getUserSetting(self::SOURCES_CARD_ID), ['0', 0], true),
            'add' => false,
            'edit' => false,
            'collapse' => true,
            'templateFile' => '@' . self::TEMPLATE_NAMESPACE . '/source_documents_card.html.twig',
            'templateVariables' => [
                'auth' => false,
                'pid' => $pid,
                'csrfToken' => CsrfUtils::collectCsrfToken($session),
                'correlationId' => CorrelationId::mint()->value,
                'documentEndpoint' => $moduleUrl . '/public/document.php',
                'uploadEndpoint' => $moduleUrl . '/public/upload.php',
                'statusEndpoint' => $moduleUrl . '/public/upload-status.php',
                // D60: quarantines, on card load, a wrong-patient copy the
                // status poll never saw finish (public/reconcile.php).
                'reconcileEndpoint' => $moduleUrl . '/public/reconcile.php',
                'askEndpoint' => $moduleUrl . '/public/ask.php',
                'questionMaxLength' => FollowUpQuestion::MAX_LENGTH,
                'maxDocuments' => AskAnswer::MAX_DOCUMENTS,
                'stylesheetUrl' => $moduleUrl . '/public/assets/w2/source-card.css?v=' . $version,
                'scriptUrl' => $moduleUrl . '/public/assets/w2/source-card.mjs?v=' . $version,
                // No query string: PDF.js resolves its worker relative to
                // itself, and two URLs would load the library twice.
                'pdfLibraryUrl' => $moduleUrl . '/public/vendor/pdfjs/pdf.min.mjs',
                'pdfWorkerUrl' => $moduleUrl . '/public/vendor/pdfjs/pdf.worker.min.mjs',
            ],
        ]);
    }

    /**
     * The newest modification time of the card scripts, so an edit to any w2
     * file reloads source-card.mjs and the stylesheet. The modules
     * source-card.mjs imports are fetched by their plain URLs and follow the
     * server's cache headers; a query string on the importer does not reach
     * them.
     */
    private function assetVersion(): string
    {
        $assets = dirname(__DIR__) . '/public/assets';
        $files = array_merge(
            [$assets . '/pre-visit-brief-card.js'],
            glob($assets . '/w2/*.{mjs,css}', GLOB_BRACE) ?: [],
        );
        $newest = 0;
        foreach ($files as $file) {
            $mtime = filemtime($file);
            $newest = max($newest, $mtime === false ? 0 : $mtime);
        }
        return (string) $newest;
    }
}
