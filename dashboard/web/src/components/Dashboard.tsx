import { useEffect, useMemo, useState } from 'react';
import { ApiError, type Fetcher } from '../api';
import {
  entries,
  type AllergyIntolerance,
  type Bundle,
  type CareTeam,
  type Condition,
  type MedicationRequest,
  type Observation,
  type Organization,
  type Patient,
  type Practitioner,
} from '../fhir/types';
import { mapAllergies, mapMedications, mapPrescriptions, mapProblems } from '../mapping/clinical';
import { careTeamReferences, mapCareTeams, organizationName, practitionerName, type ResolvedName } from '../mapping/careTeam';
import { mapLabs, mapTrends } from '../mapping/labs';
import { mapPatientHeader } from '../mapping/patient';
import { todayYmd } from '../mapping/format';
import { useAsync, type AsyncState } from '../useAsync';
import { AllergiesCard, CareTeamCard, LabsCard, MedicationsCard, PrescriptionsCard, ProblemsCard } from './cards';
import { DeceasedBanner, PatientHeader } from './PatientHeader';

function derive<A, B>(state: AsyncState<A>, f: (a: A) => B): AsyncState<B> {
  return state.status === 'ready' ? { status: 'ready', data: f(state.data) } : state;
}

/**
 * The patient dashboard. Six reads start together on mount, one per
 * section (MedicationRequest feeds both Medications and Prescriptions),
 * and each section renders as soon as its own read settles.
 */
export function Dashboard({ patientId, fetcher, today = todayYmd() }: { patientId: string; fetcher: Fetcher; today?: string }) {
  const q = encodeURIComponent(patientId);
  const patient = useAsync(`p:${patientId}`, (s) => fetcher<Patient>(`/Patient/${q}`, s));
  const allergies = useAsync(`a:${patientId}`, (s) => fetcher<Bundle<AllergyIntolerance>>(`/AllergyIntolerance?patient=${q}`, s));
  const conditions = useAsync(`c:${patientId}`, (s) => fetcher<Bundle<Condition>>(`/Condition?patient=${q}&category=problem-list-item`, s));
  const meds = useAsync(`m:${patientId}`, (s) => fetcher<Bundle<MedicationRequest>>(`/MedicationRequest?patient=${q}`, s));
  const careTeams = useAsync(`t:${patientId}`, (s) => fetcher<Bundle<CareTeam>>(`/CareTeam?patient=${q}`, s));
  const labs = useAsync(`o:${patientId}`, (s) => fetcher<Bundle<Observation>>(`/Observation?patient=${q}&category=laboratory`, s));

  const header = useMemo(() => derive(patient, (p) => mapPatientHeader(p, today)), [patient, today]);
  const allergyView = useMemo(() => derive(allergies, (b) => mapAllergies(entries(b))), [allergies]);
  const problemView = useMemo(() => derive(conditions, (b) => mapProblems(entries(b))), [conditions]);
  const medView = useMemo(() => derive(meds, (b) => mapMedications(entries(b))), [meds]);
  const rxView = useMemo(() => derive(meds, (b) => mapPrescriptions(entries(b))), [meds]);
  const teamView = useMemo(() => derive(careTeams, (b) => mapCareTeams(entries(b))), [careTeams]);
  const labView = useMemo(() => derive(labs, (b) => ({ rows: mapLabs(entries(b)), trends: mapTrends(entries(b)) })), [labs]);
  const names = useCareTeamNames(teamView, fetcher);

  return (
    <div className="patient-demographic" data-patient={patientId}>
      <PatientHeader state={header} />
      <div className="container-fluid mb-2">
        <h2 className="h5 font-weight-normal my-3 ml-2">Medical Record Dashboard{header.status === 'ready' ? ` - ${header.data.name}` : ''}</h2>
        {header.status === 'ready' && <DeceasedBanner p={header.data} />}
        <div className="main mb-1">
          <div className="row">
            <div className="p-1 col-md-4">
              <AllergiesCard state={allergyView} />
            </div>
            <div className="p-1 col-md-4">
              <ProblemsCard state={problemView} />
            </div>
            <div className="p-1 col-md-4">
              <MedicationsCard state={medView} />
            </div>
            <div className="col m-0 p-0 mx-1">
              <PrescriptionsCard state={rxView} />
            </div>
          </div>
          <div className="row">
            <div className="col-12 m-0 p-0 px-2">
              <CareTeamCard state={teamView} names={names} />
            </div>
          </div>
          <div className="row">
            <div className="col-12 m-0 p-0 px-2">
              <LabsCard state={labView} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Names for care-team members and facilities, read in one parallel batch
 * once the teams are known. OpenEMR refuses these reads to a clinician
 * without the admin/users ACL (403); that is shown per cell, not as a
 * failed card.
 */
function useCareTeamNames(teams: AsyncState<ReturnType<typeof mapCareTeams>>, fetcher: Fetcher): Map<string, ResolvedName> {
  const refs = teams.status === 'ready' ? careTeamReferences(teams.data) : [];
  const key = refs.join('|');
  const [names, setNames] = useState<Map<string, ResolvedName>>(new Map());
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    setNames(new Map(refs.map((r) => [r, { kind: 'loading' }])));
    void Promise.all(
      refs.map(async (ref): Promise<[string, ResolvedName]> => {
        try {
          const [type, id] = ref.split('/');
          const r = await fetcher<Practitioner | Organization>(`/${type}/${encodeURIComponent(id!)}`, controller.signal);
          const text = r.resourceType === 'Practitioner' ? practitionerName(r) : organizationName(r as Organization);
          return [ref, text ? { kind: 'name', text } : { kind: 'unavailable' }];
        } catch (e) {
          return [ref, e instanceof ApiError && (e.status === 403 || e.code === 'upstream_forbidden' || e.code === 'upstream_unauthorized') ? { kind: 'restricted' } : { kind: 'unavailable' }];
        }
      }),
    ).then((pairs) => {
      if (!controller.signal.aborted) setNames(new Map(pairs));
    });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return names;
}
