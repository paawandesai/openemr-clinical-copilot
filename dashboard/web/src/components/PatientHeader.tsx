import { errorMessage } from '../api';
import type { PatientHeaderView } from '../mapping/patient';
import type { AsyncState } from '../useAsync';
import pictureUrl from '../assets/patient-picture-default.png';

/**
 * The patient bar from OpenEMR's tabbed shell (patient_data_template.php,
 * default `patient_name_display`): photo, name, (MRN), and
 * "DOB: … Age: …". The requirement adds sex and active status, which the
 * PHP bar does not show (sex is on the Demographics card; see G2 for why
 * "active" is only as informative as OpenEMR's FHIR makes it).
 */
export function PatientHeader({ state }: { state: AsyncState<PatientHeaderView> }) {
  return (
    <div className="pt-bar d-lg-inline-flex w-100" data-section="patient-header">
      <div className="flex-fill">
        <div className="float-left mx-2">
          <div className="patientPicture">
            {/* OpenEMR does not expose the photo document as Patient.photo (G12): the default silhouette, as PHP shows when there is none. */}
            <img src={pictureUrl} className="img-thumbnail" width={75} height={75} alt="" />
          </div>
        </div>
        <div className="form-group">
          {state.status === 'loading' && (
            <div className="spinner-border spinner-border-sm" role="status">
              <span className="sr-only">Loading...</span>
            </div>
          )}
          {state.status === 'error' && (
            <div className="alert alert-danger p-1 d-inline-block" role="alert" data-state="error">
              {errorMessage(state.error)}
            </div>
          )}
          {state.status === 'ready' && <HeaderBody p={state.data} />}
        </div>
      </div>
    </div>
  );
}

function HeaderBody({ p }: { p: PatientHeaderView }) {
  const status = p.deceased ? 'Deceased' : p.active === false ? 'Inactive' : p.active === true ? 'Active' : 'Unknown';
  return (
    <>
      <div className="d-inline">
        <span className="ptName" data-field="name">
          <span>{p.name}</span>{' '}
          <span className="text-muted">
            (<span data-field="mrn">{p.mrn ?? 'no MRN'}</span>)
          </span>
        </span>
      </div>
      <div className="mt-2">
        <span data-field="dob">
          DOB: {p.dob} {p.ageLabel}: {p.age}
        </span>
        <span className="mx-2 text-muted">|</span>
        <span data-field="sex">Sex: {p.sex || 'Unknown'}</span>
        <span className="mx-2 text-muted">|</span>
        <span data-field="status" title="Patient.active as OpenEMR reports it. OpenEMR sets it for every patient, so only a deceased patient shows a different status.">
          Status: {status}
        </span>
      </div>
    </>
  );
}

/** partials/deceased.html.twig: the red banner card. */
export function DeceasedBanner({ p }: { p: PatientHeaderView }) {
  if (!p.deceased) return null;
  const text = p.deceased.date ? `Deceased - ${p.deceased.date} (${p.deceased.ago})` : 'Deceased';
  return (
    <section className="card bg-danger text-white" data-section="deceased">
      <div className="card-body p-1">
        <div className="card-title mb-0 d-flex p-1 justify-content-between">
          <strong>{text}</strong>
        </div>
      </div>
    </section>
  );
}
