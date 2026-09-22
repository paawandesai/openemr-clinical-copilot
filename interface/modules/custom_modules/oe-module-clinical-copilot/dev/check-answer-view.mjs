/**
 * Scripted check: does the rendered answer view keep the invariants it is
 * supposed to keep?
 *
 * The overlay check (`check-overlay-alignment.mjs`) asks whether boxes land
 * on the right ink. This one asks the other half of work order 11
 * requirement 2, of the DOM that is actually on screen for all six samples:
 *
 * 1. **Patient facts and guideline evidence are in visibly separate
 *    sections** (invariant 3), and no claim's chips appear under the wrong
 *    heading. Checked by reading each section's chips and confirming their
 *    source type matches the section.
 *
 * 2. **Chart and document facts are separated too** (D17, D2, invariant 5),
 *    so an intake form's medication list can be seen not to have been
 *    merged into the record.
 *
 * 3. **Nothing unsupported is rendered as a fact** (invariant 4). Every
 *    `field_path` in `answer.unsupported` must appear only inside the
 *    unsupported section — never as a claim statement in the chart or
 *    document sections.
 *
 * 4. **A blank flag column never reads as normal** (D20). For every row
 *    whose `reported` is `not_reported` or `unrecognised`, the rendered
 *    cell must not contain the word "normal", and the outcome cell must
 *    not say the check agreed. The comparable count and its denominator
 *    must both be on screen.
 *
 * 5. **The chip count matches the citation count**, grouped by field — a
 *    claim cannot show fewer sources than it rests on.
 *
 *   node .../dev/check-answer-view.mjs [--chrome PATH]
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import {existsSync, readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

import {openFixturePage} from './browser.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, 'fixtures');

/**
 * Read the rendered view back out of the page.
 *
 * Runs in the browser and returns plain data: which section each chip is
 * in, the text of every claim statement, and the flag table cell by cell.
 * Deliberately reads the DOM rather than the view model — the question is
 * what a clinician sees, not what the JSON said.
 */
const INSPECT = `(() => {
    const root = document.querySelector('.ccw2-answer-slot');
    const read = (selector) => [...root.querySelectorAll(selector)];
    const sections = read('.ccw2-section').map((section) => ({
        kind: [...section.classList].find((c) => c.startsWith('ccw2-section--')).replace('ccw2-section--', ''),
        heading: section.querySelector('.ccw2-section-heading').textContent,
        statements: [...section.querySelectorAll('.ccw2-claim-statement')].map((p) => p.textContent),
        chips: [...section.querySelectorAll('.ccw2-chip')].map((chip) => ({
            sourceType: chip.dataset.sourceType,
            sourceId: chip.dataset.sourceId,
            field: chip.dataset.field,
            text: chip.textContent,
        })),
        notes: [...section.querySelectorAll('.ccw2-section-note')].map((p) => p.textContent),
    }));
    const flags = read('.ccw2-flags tbody tr').map((row) => ({
        cells: [...row.children].map((cell) => cell.textContent),
        classes: [...row.children].map((cell) => cell.className),
    }));
    return {sections, flags, view: window.ccw2Fixture.view};
})()`;

function checkScenario(rendered) {
    const failures = [];
    const {sections, flags, view} = rendered;
    const byKind = new Map(sections.map((section) => [section.kind, section]));

    for (const kind of ['chart', 'document', 'guideline']) {
        if (!byKind.has(kind)) {
            failures.push(`no ${kind} section was rendered`);
        }
    }
    if (view.answer.unsupported.length && !byKind.has('unsupported')) {
        failures.push('the answer carries unsupported fields but no unsupported section was rendered');
    }
    if (view.lab_flags.length && !byKind.has('flags')) {
        failures.push('the answer carries lab flags but no flag table was rendered');
    }

    // 1 + 2: every chip sits under the heading for its own source type.
    for (const kind of ['chart', 'document', 'guideline']) {
        const section = byKind.get(kind);
        if (!section) {
            continue;
        }
        for (const chip of section.chips) {
            if (chip.sourceType !== kind) {
                failures.push(`a ${chip.sourceType} chip (${chip.field}) is under the ${kind} heading`);
            }
        }
    }
    const guideline = byKind.get('guideline');
    if (guideline && !/not about this patient/i.test(guideline.heading)) {
        failures.push(`the guideline heading does not say it is not about this patient: "${guideline.heading}"`);
    }
    const documentSection = byKind.get('document');
    // Heading or note: the requirement is that a reader sees it, not where.
    const documentWords = documentSection
        ? [documentSection.heading, ...documentSection.notes].join(' ')
        : '';
    if (documentSection && !/(not|never) merged into the chart/i.test(documentWords)) {
        failures.push(
            `the document section never says its contents are not merged into the chart: "${documentWords}"`
        );
    }

    // 3: an unsupported field_path never appears as a fact.
    const factStatements = ['chart', 'document']
        .map((kind) => byKind.get(kind))
        .filter(Boolean)
        .flatMap((section) => section.statements);
    for (const field of view.answer.unsupported) {
        for (const statement of factStatements) {
            if (statement.includes(field.field_path)) {
                failures.push(`unsupported field ${field.field_path} appears in a fact: "${statement}"`);
            }
        }
        // `unverified_value` is the only thing the model read, and it is
        // null for both statuses the samples carry. If one ever arrives, it
        // must not be presented bare.
        if (field.unverified_value !== null && factStatements.some((s) => s.includes(field.unverified_value))) {
            failures.push(`the unverified reading of ${field.field_path} appears as a fact`);
        }
    }

    // 4: D20 — nothing blank reads as normal, nothing silent reads as agreement.
    const silent = view.lab_flags.filter(
        (flag) => !['normal', 'high', 'low', 'abnormal'].includes(flag.cross_check.reported)
    );
    for (const [index, flag] of view.lab_flags.entries()) {
        const row = flags[index];
        if (!row) {
            failures.push(`flag row ${index} (${flag.test_name}) was not rendered`);
            continue;
        }
        const [, reportedCell, , outcomeCell] = row.cells;
        const comparable = ['normal', 'high', 'low', 'abnormal'].includes(flag.cross_check.reported);
        if (!comparable) {
            if (/normal/i.test(reportedCell)) {
                failures.push(
                    `${flag.test_name}: reported is ${flag.cross_check.reported} but the cell reads "${reportedCell}"`
                );
            }
            if (/agree/i.test(outcomeCell)) {
                failures.push(
                    `${flag.test_name}: reported is ${flag.cross_check.reported} but the check cell reads "${outcomeCell}"`
                );
            }
        }
        if (flag.cross_check.reported === 'not_reported' && !/not reported/i.test(reportedCell)) {
            failures.push(`${flag.test_name}: a blank flag column renders as "${reportedCell}"`);
        }
    }
    if (view.lab_flags.length) {
        const note = (byKind.get('flags')?.notes || []).join(' ');
        const comparable = view.lab_flags.length - silent.length;
        if (!note.includes(String(view.lab_flags.length)) || !note.includes(String(comparable))) {
            failures.push(
                `the flag table does not show both the comparable count (${comparable}) and its ` +
                    `denominator (${view.lab_flags.length}): "${note}"`
            );
        }
    }

    // 5: one chip per (source, field) the claims actually cite.
    const expected = new Set();
    for (const claim of [...view.answer.patient_facts, ...view.answer.guideline_evidence]) {
        for (const citation of claim.citations) {
            expected.add(`${citation.source_type}|${citation.source_id}|${citation.field_or_chunk_id}`);
        }
    }
    const drawn = new Set();
    for (const kind of ['chart', 'document', 'guideline']) {
        for (const chip of byKind.get(kind)?.chips || []) {
            drawn.add(`${chip.sourceType}|${chip.sourceId}|${chip.field}`);
        }
    }
    for (const key of expected) {
        if (!drawn.has(key)) {
            failures.push(`no chip was drawn for ${key}`);
        }
    }

    return {failures, sections: sections.length, flags: flags.length, chips: drawn.size};
}

async function main() {
    const args = process.argv.slice(2);
    const chromeArg = args.indexOf('--chrome') === -1 ? null : args[args.indexOf('--chrome') + 1];

    if (!existsSync(join(FIXTURES, 'manifest.json'))) {
        process.stderr.write('No fixtures. Build them first:\n  cd agent && python scripts/build_ui_fixtures.py\n');
        return 1;
    }
    const manifest = JSON.parse(readFileSync(join(FIXTURES, 'manifest.json'), 'utf8'));

    let page;
    try {
        page = await openFixturePage(chromeArg);
    } catch (error) {
        process.stderr.write(`${error.message}\n`);
        return 1;
    }

    let failed = 0;
    try {
        process.stdout.write('answer view check — separation, disclosure, D20 flags\n\n');
        for (const entry of manifest.scenarios) {
            await page.evaluate(`window.ccw2Fixture.load(${JSON.stringify(entry.scenario_id)})`);
            const result = checkScenario(await page.evaluate(INSPECT));
            process.stdout.write(
                `${(result.failures.length ? 'FAIL' : 'ok').padEnd(5)} ${entry.scenario_id.padEnd(32)} ` +
                    `${String(result.sections).padStart(2)} sections, ${String(result.chips).padStart(4)} chips, ` +
                    `${String(result.flags).padStart(3)} flag rows\n`
            );
            for (const failure of result.failures.slice(0, 12)) {
                process.stdout.write(`        ${failure}\n`);
            }
            if (result.failures.length > 12) {
                process.stdout.write(`        ... and ${result.failures.length - 12} more\n`);
            }
            failed += result.failures.length;
        }
        process.stdout.write(
            failed === 0 ? `\nAll ${manifest.scenarios.length} scenarios hold.\n` : `\n${failed} problem(s).\n`
        );
    } finally {
        await page.close();
    }
    return failed === 0 ? 0 : 1;
}

process.exitCode = await main();
