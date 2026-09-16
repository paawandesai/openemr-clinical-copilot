/**
 * Clinical Co-Pilot: Pre-Visit Brief card on the patient dashboard.
 *
 * Requests the brief when the card is open on page load (the chart opening is
 * the trigger, docs/USERS.md Use Case 1), or when it is first expanded, and on
 * Regenerate. Everything shown is inserted as text, never as HTML.
 *
 * The brief text comes from the agent's render_brief()
 * (agent/app/graph/pre_visit_brief.py). Its lines are styled here by shape;
 * a line that matches no known shape is still shown, as plain text.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */
(function (window) {
    'use strict';

    const TITLE_LINE = 'Pre-Visit Brief';
    const SECTION_LINE = /^(\S[^:]*):\s*(.*)$/;
    const ITEM_LINE = /^\s+-\s+(.*)$/;
    const UNVERIFIED_ITEM = /^UNVERIFIED: "(.*)" \((.+)\)$/;
    const OFF_BRIEF_ITEM = /^(.*) \((on file, but not an? .+)\)$/;
    const OMITTED_ITEM = /^\d+ more .+ not included in this brief$/;
    const NONE_ACTIVE = /^none active(?: \((.+)\))?$/;
    const UNVERIFIED_SUMMARY = /could not be verified against the chart/;

    function node(tag, className, text) {
        const element = window.document.createElement(tag);
        if (className) {
            element.className = className;
        }
        if (text !== undefined) {
            element.textContent = text;
        }
        return element;
    }

    function fill(template, values) {
        return template.replace(/%(\w+)%/g, function (match, key) {
            return Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match;
        });
    }

    function renderHeading(title, state, strings) {
        const heading = node('div', 'd-flex flex-wrap align-items-baseline mt-2 mb-1');
        heading.appendChild(node('span', 'font-weight-bold mr-2', title));
        if (state === '') {
            return heading;
        }
        const noneActive = NONE_ACTIVE.exec(state);
        if (state === 'not recorded') {
            heading.appendChild(node('span', 'badge badge-secondary', strings.notRecorded));
        } else if (noneActive) {
            heading.appendChild(node('span', 'badge badge-info mr-2', strings.noneActive));
            if (noneActive[1]) {
                heading.appendChild(node('span', 'small text-muted', noneActive[1]));
            }
        } else {
            heading.appendChild(node('span', 'small', state));
        }
        return heading;
    }

    function renderItem(text, strings) {
        const unverified = UNVERIFIED_ITEM.exec(text);
        if (unverified) {
            const item = node('li', 'border-left border-warning pl-2 mb-1');
            item.appendChild(node('span', 'badge badge-warning mr-1', strings.unverified));
            item.appendChild(node('span', 'mr-1', unverified[1]));
            item.appendChild(node('span', 'small text-muted', '(' + unverified[2] + ')'));
            return item;
        }
        const offBrief = OFF_BRIEF_ITEM.exec(text);
        if (offBrief) {
            const item = node('li', 'mb-1');
            item.appendChild(node('span', 'mr-1', offBrief[1]));
            item.appendChild(node('span', 'small text-muted', '(' + offBrief[2] + ')'));
            return item;
        }
        if (OMITTED_ITEM.test(text)) {
            return node('li', 'mb-1 small text-muted', text);
        }
        return node('li', 'mb-1', text);
    }

    function renderBrief(container, text, strings) {
        container.replaceChildren();
        let list = null;
        text.split('\n').forEach(function (line, index) {
            if (index === 0 && line === TITLE_LINE) {
                return; // the card title already says it
            }
            if (line.trim() === '') {
                list = null;
                return;
            }
            const item = ITEM_LINE.exec(line);
            if (item) {
                if (list === null) {
                    list = node('ul', 'mb-1 pl-4');
                    container.appendChild(list);
                }
                list.appendChild(renderItem(item[1], strings));
                return;
            }
            list = null;
            if (UNVERIFIED_SUMMARY.test(line)) {
                container.appendChild(node('div', 'alert alert-warning py-1 px-2 mt-2 mb-1 small', line));
                return;
            }
            const section = SECTION_LINE.exec(line);
            if (section) {
                container.appendChild(renderHeading(section[1], section[2], strings));
                return;
            }
            container.appendChild(node('p', 'mb-1', line));
        });
    }

    // Colour on the icon only: warning-yellow text on a light card is unreadable.
    function renderVerification(target, verification, strings) {
        if (verification.claims === 0) {
            target.replaceChildren(node('span', 'text-muted', strings.noClaims));
        } else if (verification.unverified === 0) {
            target.replaceChildren(
                node('i', 'fa fa-check-circle text-success mr-1'),
                node('span', '', fill(strings.verifiedAll, verification))
            );
        } else {
            target.replaceChildren(
                node('i', 'fa fa-exclamation-triangle text-warning mr-1'),
                node('span', 'font-weight-bold', fill(strings.verifiedSome, verification))
            );
        }
    }

    function initPreVisitBriefCard(root, strings) {
        if (!root) {
            return;
        }
        const status = root.querySelector('.cc-status');
        const error = root.querySelector('.cc-error');
        const brief = root.querySelector('.cc-brief');
        const verification = root.querySelector('.cc-verification');
        const regenerate = root.querySelector('.cc-regenerate');
        const collapse = window.document.getElementById(root.dataset.collapseId);
        let inFlight = false;
        let requested = false;

        function showError(message, correlationId) {
            error.replaceChildren(node('div', '', message));
            if (correlationId) {
                error.appendChild(node('div', 'text-monospace mt-1', strings.reference + ': ' + correlationId));
            }
            error.classList.remove('d-none');
        }

        function generate() {
            if (inFlight) {
                return;
            }
            inFlight = true;
            requested = true;
            regenerate.disabled = true;
            error.classList.add('d-none');
            status.replaceChildren(node('i', 'fa fa-circle-notch fa-spin mr-1'), window.document.createTextNode(strings.generating));

            if (window.top && typeof window.top.restoreSession === 'function') {
                window.top.restoreSession();
            }
            const body = new window.FormData();
            body.append('pid', root.dataset.pid);
            body.append('csrf_token_form', root.dataset.csrfToken);

            window.fetch(root.dataset.endpoint, {method: 'POST', credentials: 'same-origin', body: body})
                .then(function (response) {
                    return response.json().catch(function () {
                        return null;
                    }).then(function (payload) {
                        return {ok: response.ok, payload: payload};
                    });
                })
                .then(function (result) {
                    const payload = result.payload;
                    status.replaceChildren();
                    if (result.ok && payload && payload.status === 'ok' && typeof payload.brief === 'string') {
                        renderBrief(brief, payload.brief, strings);
                        brief.classList.remove('d-none');
                        renderVerification(verification, payload.verification, strings);
                        status.textContent = strings.generatedAt + ' ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
                        return;
                    }
                    // A failed regenerate must not leave an earlier brief looking current.
                    brief.classList.add('d-none');
                    verification.textContent = '';
                    if (payload && typeof payload.message === 'string') {
                        showError(payload.message, typeof payload.correlation_id === 'string' ? payload.correlation_id : null);
                    } else {
                        showError(strings.unreachable, null);
                    }
                })
                .catch(function () {
                    status.replaceChildren();
                    brief.classList.add('d-none');
                    verification.textContent = '';
                    showError(strings.unreachable, null);
                })
                .finally(function () {
                    inFlight = false;
                    regenerate.disabled = false;
                });
        }

        regenerate.addEventListener('click', generate);
        if (!collapse || collapse.classList.contains('show')) {
            generate();
        } else if (window.jQuery) {
            // Collapsed by the user's saved preference: wait until they open it.
            window.jQuery(collapse).on('shown.bs.collapse', function () {
                if (!requested) {
                    generate();
                }
            });
            regenerate.disabled = false;
        } else {
            regenerate.disabled = false;
        }
    }

    window.oeClinicalCopilot = window.oeClinicalCopilot || {};
    window.oeClinicalCopilot.initPreVisitBriefCard = initPreVisitBriefCard;
})(window);
