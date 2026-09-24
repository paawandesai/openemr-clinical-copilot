// Captures FHIR responses from the LOCAL OpenEMR (synthetic patients only)
// as unit-test fixtures. Usage:
//   FHIR_TOKEN=<physician bearer token> NODE_EXTRA_CA_CERTS=<local cert> \
//     node scripts/capture-fixtures.mjs <patient-uuid> <name>
// The Social Security identifier is replaced with a fixed fake value, so the
// fixture still proves the dashboard never shows it as the MRN.
import { writeFileSync, mkdirSync } from 'node:fs';
const [uuid, name] = process.argv.slice(2);
const base = process.env.FHIR_BASE ?? 'https://localhost:9300/apis/default/fhir';
if (!uuid || !name || !process.env.FHIR_TOKEN) throw new Error('usage: FHIR_TOKEN=… node scripts/capture-fixtures.mjs <uuid> <name>');
if (!base.startsWith('https://localhost:')) throw new Error('fixtures are captured from the local stack only');
const get = async (path) => {
  const r = await fetch(base + path, { headers: { Authorization: `Bearer ${process.env.FHIR_TOKEN}`, Accept: 'application/fhir+json' } });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
};
const scrub = (p) => {
  for (const id of p.identifier ?? []) if (id.type?.coding?.some((c) => c.code === 'SS')) id.value = '999-99-9999';
  return p;
};
const out = {
  Patient: scrub(await get(`/Patient/${uuid}`)),
  AllergyIntolerance: await get(`/AllergyIntolerance?patient=${uuid}`),
  Condition: await get(`/Condition?patient=${uuid}&category=problem-list-item`),
  MedicationRequest: await get(`/MedicationRequest?patient=${uuid}`),
  CareTeam: await get(`/CareTeam?patient=${uuid}`),
  Observation: await get(`/Observation?patient=${uuid}&category=laboratory`),
};
mkdirSync('test/fixtures', { recursive: true });
writeFileSync(`test/fixtures/${name}.json`, JSON.stringify({ capturedAt: new Date().toISOString().slice(0, 10), source: 'local OpenEMR dev stack, synthetic data', patient: uuid, ...out }, null, 1) + '\n');
console.log(name, Object.fromEntries(Object.entries(out).map(([k, v]) => [k, v.total ?? 1])));
