import { useState } from 'react';
import type { AsyncState } from '../useAsync';
import type { AllergyView, MedicationView, PrescriptionView, ProblemView } from '../mapping/clinical';
import type { CareTeamView, ResolvedName } from '../mapping/careTeam';
import { trendKey, type LabRowView, type LabTrend } from '../mapping/labs';
import { Card, EmptyRow } from './Card';
import { Sparkline } from './Sparkline';

/**
 * The PHP cards say "No Known Allergies" when the list was ever touched and
 * "Nothing Recorded" otherwise. FHIR carries no "touched" flag (G9), and
 * "No Known Allergies" is a clinical assertion, so the port never makes it.
 */
export function AllergiesCard({ state }: { state: AsyncState<AllergyView[]> }) {
  return (
    <Card id="allergy_ps_expand" title="Allergies" state={state}>
      {(list) => (
        <div className="list-group list-group-flush pami-list">
          {list.length === 0 && <EmptyRow text="Nothing Recorded" />}
          {list.map((a) => (
            <div className="list-group-item p-1" key={a.id}>
              <div className="d-flex w-100 justify-content-between">
                <div className="flex-fill" title={a.tooltip}>
                  {a.title}
                  {a.criticality && (
                    <>
                      {' '}
                      (<span className={a.highlight ? 'bg-warning font-weight-bold px-1' : ''}>{a.criticality}</span>)
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function ProblemsCard({ state }: { state: AsyncState<ProblemView[]> }) {
  return (
    <Card id="medical_problem_ps_expand" title="Medical Problems" state={state}>
      {(list) => (
        <div className="list-group list-group-flush pami-list">
          {list.length === 0 && <EmptyRow text="Nothing Recorded" />}
          {list.map((p) => (
            <div className="list-group-item py-1 px-1" key={p.id}>
              {p.title}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function MedicationsCard({ state }: { state: AsyncState<MedicationView[]> }) {
  return (
    <Card id="medication_ps_expand" title="Medications" state={state}>
      {(list) => (
        <div className="list-group list-group-flush pami-list">
          {list.length === 0 && <EmptyRow text="Nothing Recorded" />}
          {list.map((m) => (
            <div className="list-group-item p-0 pl-1" key={m.id}>
              <span className="font-weight-normal">{m.title}</span> <span>{m.instructions}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/** templates/prescription/general_fragment.html: the same five columns, "None" when empty. */
export function PrescriptionsCard({ state }: { state: AsyncState<PrescriptionView[]> }) {
  return (
    <Card id="prescriptions_ps_expand" title="Prescriptions" state={state}>
      {(list) =>
        list.length === 0 ? (
          <span data-state="empty">None</span>
        ) : (
          <div className="table-responsive">
            <table className="table table-sm table-striped">
              <thead>
                <tr>
                  <th>Drug</th>
                  <th>Details</th>
                  <th>Qty</th>
                  <th>Refills</th>
                  <th>Filled</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id}>
                    <td>{r.drug}</td>
                    <td>{r.details}</td>
                    <td>{r.quantity}</td>
                    <td>{r.refills}</td>
                    <td>{r.filled}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }
    </Card>
  );
}

function NameCell({ name }: { name: ResolvedName | undefined }) {
  if (!name || name.kind === 'loading') return <span className="text-muted">…</span>;
  if (name.kind === 'name') return <>{name.text}</>;
  if (name.kind === 'restricted') {
    return (
      <span className="text-muted" title="OpenEMR allows Practitioner and Organization reads only to users with the admin/users permission.">
        Not available to your role
      </span>
    );
  }
  return <span className="text-muted">Unavailable</span>;
}

/**
 * manage_care_team.html.twig in view mode: team name with its status
 * badge, then Type | Member | Role | Facility | Since | Status | Note.
 * Member status and note are not in OpenEMR's FHIR CareTeam (G10), so those
 * columns stay, empty, to keep the table the clinician knows.
 */
export function CareTeamCard({ state, names }: { state: AsyncState<CareTeamView[]>; names: Map<string, ResolvedName> }) {
  return (
    <Card id="careteam_ps_expand" title="Care Team" state={state} containerClass="card">
      {(teams) => (
        <div className="container-fluid">
          <div className="row">
            <div className="col-12">
              {teams.length === 0 && (
                <div className="table-responsive care-team mt-3">
                  <table className="table table-sm table-striped">
                    <CareTeamHead />
                    <tbody>
                      <tr data-state="empty">
                        <td colSpan={7} className="text-muted">
                          No care team recorded.
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}
              {teams.map((t) => (
                <div className="mt-3" key={t.id}>
                  <div className="form-group p-0 col-md-3">
                    <h5>
                      {t.name}{' '}
                      {t.name && <span className={`badge ${t.statusBadge}`}>{t.statusLabel}</span>}
                    </h5>
                  </div>
                  <div className="table-responsive care-team">
                    <table className="table table-sm table-striped">
                      <CareTeamHead />
                      <tbody>
                        {t.members.map((m) => (
                          <tr key={m.key}>
                            <td>
                              <span className={`badge ${m.typeBadge}`}>{m.typeLabel}</span>
                            </td>
                            <td>{m.memberRef ? <NameCell name={names.get(m.memberRef)} /> : ''}</td>
                            <td>{m.role}</td>
                            <td>{m.facilityRef ? <NameCell name={names.get(m.facilityRef)} /> : ''}</td>
                            <td>{m.since}</td>
                            <td></td>
                            <td></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

function CareTeamHead() {
  return (
    <thead className="thead-light bg-light text-dark">
      <tr>
        <th>Type</th>
        <th>Member</th>
        <th>Role</th>
        <th>Facility</th>
        <th>Since</th>
        <th>Status</th>
        <th>Note</th>
      </tr>
    </thead>
  );
}

const FIRST_ROWS = 30;

/**
 * The additional section. The PHP Labs card shows only the most recent
 * procedure and links to labdata.php; this section shows the results
 * themselves (value, unit, range, flag, date), newest first, with a trend
 * beside the latest result of any analyte measured more than once.
 */
export function LabsCard({ state }: { state: AsyncState<{ rows: LabRowView[]; trends: Map<string, LabTrend> }> }) {
  const [showAll, setShowAll] = useState(false);
  return (
    <Card id="labs_ps_expand" title="Lab Results" state={state} containerClass="card">
      {({ rows, trends }) => {
        if (rows.length === 0) return <span data-state="empty">No lab data documented.</span>;
        const seen = new Set<string>();
        const shown = showAll ? rows : rows.slice(0, FIRST_ROWS);
        return (
          <>
            <div className="table-responsive">
              <table className="table table-sm table-striped lab-results">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Test</th>
                    <th className="text-right">Value</th>
                    <th>Units</th>
                    <th>Reference range</th>
                    <th>Flag</th>
                    <th>Trend</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => {
                    const key = trendKey(r.code, r.unit);
                    const trend = r.code && !seen.has(key) ? trends.get(key) : undefined;
                    if (r.code) seen.add(key);
                    return (
                      <tr key={r.id} data-code={r.code}>
                        <td className="text-nowrap">{r.date}</td>
                        <td>
                          {r.test}
                          {r.status && r.status !== 'final' && <span className="badge badge-secondary ml-1">{r.status}</span>}
                          {r.extractionId && (
                            <span className="badge badge-info ml-1" title={`Written to the chart from an uploaded lab report (${r.extractionId}); pending review in Procedures.`}>
                              From document
                            </span>
                          )}
                        </td>
                        <td className="text-right">{r.value}</td>
                        <td>{r.unit}</td>
                        <td>{r.range}</td>
                        <td className={r.flag?.abnormal ? 'text-danger font-weight-bold' : ''}>{r.flag?.label ?? ''}</td>
                        <td>{trend && <Sparkline trend={trend} />}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {rows.length > FIRST_ROWS && (
              <button type="button" className="btn btn-link btn-sm p-0 ml-1" onClick={() => setShowAll((s) => !s)}>
                {showAll ? 'Show fewer' : `Show all ${rows.length} results`}
              </button>
            )}
          </>
        );
      }}
    </Card>
  );
}
