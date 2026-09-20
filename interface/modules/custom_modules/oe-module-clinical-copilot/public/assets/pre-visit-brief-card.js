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
 * Below the brief, the follow-up conversation: each question is one POST to
 * conversation.php, which answers for the patient open in the chart and
 * nobody else. The card names its conversation with a random nonce and never
 * sees the agent's session id (src/ConversationSessionId.php). Turns are kept
 * in this page's DOM only: nothing clinical goes into browser storage.
 *
 * Every brief and every answer opens with a bar naming the patient it is
 * about (docs/audit/findings.md F-62). The name is the one in the response,
 * which the server took from the chart open in the session for that request;
 * it is never taken from this page or from the question. A response without
 * it is treated as a failure rather than shown unnamed.
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

    function hasPatient(payload) {
        return Boolean(payload.patient) && typeof payload.patient.name === 'string' && payload.patient.name !== '';
    }

    // Dark bar, full width: "asked about X, sees Answer for Y" has to be obvious at a glance.
    function renderPatientBar(template, patient, strings) {
        const bar = node('div', 'cc-patient-bar bg-dark text-white rounded px-2 py-1 mb-1');
        bar.appendChild(node('i', 'fa fa-user mr-2'));
        bar.appendChild(node('span', 'font-weight-bold', fill(template, patient)));
        if (typeof patient.date_of_birth === 'string' && patient.date_of_birth !== '') {
            bar.appendChild(node('span', 'small ml-2', fill(strings.dateOfBirth, patient)));
        }
        return bar;
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

    const TURN_BANNERS = {
        answered: {alert: 'alert-success', icon: 'fa-check-circle', text: 'turnAnswered'},
        partially_answered: {alert: 'alert-warning', icon: 'fa-exclamation-triangle', text: 'turnPartly'},
        not_in_record: {alert: 'alert-secondary', icon: 'fa-exclamation-triangle', text: 'turnNotInRecord'},
        refused_out_of_scope: {alert: 'alert-dark', icon: 'fa-ban', text: 'turnRefusedScope'},
        refused_unverifiable: {alert: 'alert-dark', icon: 'fa-ban', text: 'turnRefusedUnverifiable'}
    };
    const RESET_NOTES = {reset_patient_changed: 'resetPatientChanged', reset_expired: 'resetExpired'};
    const RECORD_LABELS = {
        Condition: 'recordsProblems',
        MedicationRequest: 'recordsMedications',
        AllergyIntolerance: 'recordsAllergies',
        Observation: 'recordsLabs',
        Encounter: 'recordsVisits'
    };
    const RULE_LABELS = {question_rule: 'ruleQuestion', context_carry: 'ruleContext'};
    const BULLET_LINE = /^\s{2}-\s+(.*)$/;
    const UNVERIFIED_CLAIM = /^UNVERIFIED:\s*([\s\S]*?)(?:\s\(([^)]*)\))?$/;

    // Not crypto.randomUUID(): that needs a secure context, and OpenEMR is also served over plain http.
    function newConversationNonce() {
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return Array.prototype.map.call(bytes, function (byte) {
            return ('0' + byte.toString(16)).slice(-2);
        }).join('');
    }

    function renderClaim(claim, strings) {
        const source = node('div', 'small text-muted text-monospace', claim.resource_type + ' · ' + claim.field + ' · ' + strings.source + ' ' + claim.source_id);
        if (claim.verified) {
            const item = node('li', 'mb-1');
            item.appendChild(node('i', 'fa fa-check-circle text-success mr-1'));
            item.appendChild(node('span', '', claim.text));
            item.appendChild(source);
            return item;
        }
        // Agent wording is 'UNVERIFIED: <claim> (<why>)'; the badge says the first word.
        const parts = UNVERIFIED_CLAIM.exec(claim.text);
        const item = node('li', 'border-left border-warning pl-2 mb-1');
        item.appendChild(node('span', 'badge badge-warning mr-1', strings.unverified));
        item.appendChild(node('span', '', parts ? parts[1] : claim.text));
        item.appendChild(node('div', 'small', strings.notVerifiable + ' ' + ((parts && parts[2]) || claim.unverified_reason || '')));
        item.appendChild(source);
        return item;
    }

    function renderRetrieval(retrieval, strings) {
        const row = node('div', 'mt-1');
        retrieval.forEach(function (read) {
            const label = strings[RECORD_LABELS[read.resource_type]] || read.resource_type;
            const count = read.considered === read.retrieved ? String(read.retrieved) : fill(strings.consideredOf, read);
            const rule = strings[RULE_LABELS[read.rule]];
            row.appendChild(node('span', 'badge badge-light border font-weight-normal mr-1', label + ': ' + count + (rule ? ' · ' + rule : '')));
        });
        return row;
    }

    /**
     * The answer text is the agent's render_turn() / render_refusal()
     * (agent/app/graph/conversation.py): a title, a headline, one bullet per
     * claim in claim order, then fixed sentences (gaps or refusals as further
     * bullets), and a closing line. The claims are drawn from the structured
     * list at the point their bullets stand; every other line is shown as the
     * agent wrote it, so nothing the agent said is dropped.
     */
    function renderTurnAnswer(container, payload, strings) {
        const info = payload.conversation;
        const counts = payload.verification;
        const refused = payload.turn_status.indexOf('refused_') === 0;
        container.replaceChildren(
            renderPatientBar(strings.answerFor, payload.patient, strings),
            node('div', 'small text-muted text-uppercase', fill(strings.turnLabel, info))
        );

        if (RESET_NOTES[info.transition]) {
            container.appendChild(node('div', 'alert alert-warning py-1 px-2 mb-1 small', strings[RESET_NOTES[info.transition]]));
        }
        if (info.context_turns > 0 && !refused) {
            container.appendChild(node('div', 'small text-muted mb-1', fill(strings.contextGiven, info)));
        }
        if (!info.state_saved) {
            container.appendChild(node('div', 'alert alert-warning py-1 px-2 mb-1 small', strings.stateNotSaved));
        }

        const banner = TURN_BANNERS[payload.turn_status] || TURN_BANNERS.not_in_record;
        const bannerBox = node('div', 'alert ' + banner.alert + ' py-1 px-2 mb-1 small');
        bannerBox.appendChild(node('i', 'fa ' + banner.icon + ' mr-1'));
        bannerBox.appendChild(node('span', '', strings[banner.text] + (counts.claims > 0 ? ' — ' + fill(strings.claimsVerified, counts) : '')));
        container.appendChild(bannerBox);

        const lines = payload.answer.split('\n');
        let claimBullets = payload.claims.length;
        if (claimBullets > 0) {
            const claims = node('ul', 'list-unstyled mb-1');
            payload.claims.forEach(function (claim) {
                claims.appendChild(renderClaim(claim, strings));
            });
            container.appendChild(claims);
        }
        let list = null;
        // Lines 0-2 are the title, a blank and the headline the banner restates.
        lines.slice(3).forEach(function (line, index, rest) {
            if (line.trim() === '') {
                list = null;
                return;
            }
            const bullet = BULLET_LINE.exec(line);
            if (bullet && claimBullets > 0) {
                claimBullets -= 1; // drawn above, from the structured claim
                return;
            }
            if (bullet) {
                if (list === null) {
                    list = node('ul', 'mb-1 pl-4 small');
                    container.appendChild(list);
                }
                list.appendChild(node('li', '', bullet[1]));
                return;
            }
            list = null;
            if (index === rest.length - 1) {
                container.appendChild(renderRetrieval(payload.retrieval, strings));
                container.appendChild(node('p', 'small text-muted mt-1 mb-1', line));
            } else if (UNVERIFIED_SUMMARY.test(line)) {
                container.appendChild(node('div', 'alert alert-warning py-1 px-2 mb-1 small', line));
            } else {
                container.appendChild(node('p', 'small mb-1', line));
            }
        });

        const raw = node('details', 'small');
        raw.appendChild(node('summary', 'text-muted', strings.rawAnswer));
        raw.appendChild(node('pre', 'small border rounded p-2 mt-1 mb-1', payload.answer));
        container.appendChild(raw);
        container.appendChild(node('div', 'small text-muted text-monospace', payload.total_seconds.toFixed(2) + 's · ' + strings.reference + ': ' + payload.correlation_id));
    }

    function initConversation(root, strings) {
        const panel = root.querySelector('.cc-conversation');
        if (!panel) {
            return {show: function () {}};
        }
        const thread = panel.querySelector('.cc-thread');
        const meta = panel.querySelector('.cc-conversation-meta');
        const form = panel.querySelector('.cc-ask');
        const input = panel.querySelector('.cc-question');
        const ask = panel.querySelector('.cc-ask-button');
        const restart = panel.querySelector('.cc-new-conversation');
        const suggestions = panel.querySelector('.cc-suggestions');
        // One conversation per card, and the card is rendered for one patient:
        // the nonce, like data-pid, never changes patient. The server does not
        // depend on that (it derives the agent session from the open chart).
        let nonce = newConversationNonce();
        let turns = 0;
        let inFlight = false;
        let closed = false;

        function request(fields) {
            if (window.top && typeof window.top.restoreSession === 'function') {
                window.top.restoreSession();
            }
            const body = new window.FormData();
            body.append('pid', root.dataset.pid);
            body.append('csrf_token_form', root.dataset.csrfToken);
            Object.keys(fields).forEach(function (key) {
                body.append(key, fields[key]);
            });
            return body;
        }

        function showMeta(info) {
            meta.replaceChildren();
            if (!info) {
                meta.appendChild(node('span', 'badge badge-light border font-weight-normal mr-1', strings.noTurns));
                return;
            }
            meta.appendChild(node('span', 'badge badge-light border font-weight-normal mr-1', fill(strings.turnCount, info)));
            meta.appendChild(node('span', 'badge badge-light border font-weight-normal mr-1', strings.remembersVerified));
            meta.appendChild(node('span', 'badge badge-light border font-weight-normal mr-1', fill(strings.keptIfIdle, {minutes: Math.max(1, Math.round(info.expires_in_seconds / 60))})));
        }

        function setBusy(busy) {
            inFlight = busy;
            input.disabled = busy || closed;
            ask.disabled = busy || closed;
            restart.disabled = busy || closed;
            suggestions.querySelectorAll('button').forEach(function (button) {
                button.disabled = busy || closed;
            });
        }

        function showTurnError(target, payload) {
            const box = node('div', 'alert alert-danger py-1 px-2 mb-1 small');
            box.appendChild(node('div', '', payload && typeof payload.message === 'string' ? payload.message : strings.unreachable));
            if (payload && typeof payload.correlation_id === 'string') {
                box.appendChild(node('div', 'text-monospace mt-1', strings.reference + ': ' + payload.correlation_id));
            }
            target.replaceChildren(box);
            // Another patient is open in this session: this card can no longer ask anything.
            if (payload && payload.error === 'patient_context_changed') {
                closed = true;
            }
        }

        function submit(question) {
            if (inFlight || closed || question === '') {
                return;
            }
            setBusy(true);
            const turn = node('div', 'cc-turn mb-3');
            const asked = node('div', 'd-flex justify-content-end mb-1');
            asked.appendChild(node('div', 'border rounded bg-light px-2 py-1', question));
            const answer = node('div', '');
            answer.appendChild(node('i', 'fa fa-circle-notch fa-spin mr-1'));
            answer.appendChild(node('span', 'small text-muted', strings.answering));
            turn.appendChild(asked);
            turn.appendChild(answer);
            thread.appendChild(turn);

            window.fetch(root.dataset.conversationEndpoint, {
                method: 'POST',
                credentials: 'same-origin',
                body: request({action: 'ask', conversation: nonce, question: question})
            })
                .then(function (response) {
                    return response.json().catch(function () {
                        return null;
                    }).then(function (payload) {
                        return {ok: response.ok, payload: payload};
                    });
                })
                .then(function (result) {
                    const payload = result.payload;
                    if (result.ok && payload && payload.status === 'ok' && typeof payload.answer === 'string' && hasPatient(payload)) {
                        renderTurnAnswer(answer, payload, strings);
                        turns += 1;
                        showMeta(payload.conversation);
                        input.value = '';
                        return;
                    }
                    showTurnError(answer, payload);
                })
                .catch(function () {
                    showTurnError(answer, null);
                })
                .finally(function () {
                    setBusy(false);
                    if (!closed) {
                        input.focus();
                    }
                });
        }

        // Best effort: the agent discards an idle conversation on its own.
        function end(useBeacon) {
            if (turns === 0 || closed) {
                return;
            }
            const body = request({action: 'end', conversation: nonce});
            if (useBeacon && window.navigator.sendBeacon) {
                window.navigator.sendBeacon(root.dataset.conversationEndpoint, body);
                return;
            }
            window.fetch(root.dataset.conversationEndpoint, {method: 'POST', credentials: 'same-origin', body: body}).catch(function () {});
        }

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            submit(input.value.trim());
        });
        suggestions.addEventListener('click', function (event) {
            if (event.target instanceof window.HTMLButtonElement) {
                submit(event.target.textContent);
            }
        });
        restart.addEventListener('click', function () {
            if (inFlight) {
                return;
            }
            end(false);
            nonce = newConversationNonce();
            turns = 0;
            thread.replaceChildren();
            showMeta(null);
            input.focus();
        });
        window.addEventListener('pagehide', function () {
            end(true);
        });
        showMeta(null);

        return {
            show: function () {
                panel.classList.remove('d-none');
            }
        };
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
        const conversation = initConversation(root, strings);
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
                    if (result.ok && payload && payload.status === 'ok' && typeof payload.brief === 'string' && hasPatient(payload)) {
                        renderBrief(brief, payload.brief, strings);
                        brief.prepend(renderPatientBar(strings.briefFor, payload.patient, strings));
                        brief.classList.remove('d-none');
                        renderVerification(verification, payload.verification, strings);
                        conversation.show();
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
