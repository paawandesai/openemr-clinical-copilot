import { useState } from 'react';
import { errorMessage, type Fetcher } from '../api';
import { entries, type Bundle, type Patient } from '../fhir/types';
import { mapPatientHeader, type PatientHeaderView } from '../mapping/patient';
import { todayYmd } from '../mapping/format';

/**
 * Choosing a patient. In OpenEMR this is the Finder tab, outside the
 * dashboard; the port needs one because it has no tabbed shell. A name
 * search, or an exact MRN when the input is a number.
 */
export function PatientFinder({ fetcher, onOpen }: { fetcher: Fetcher; onOpen: (id: string) => void }) {
  const [term, setTerm] = useState('');
  const [state, setState] = useState<{ status: 'idle' } | { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ready'; rows: PatientHeaderView[] }>({
    status: 'idle',
  });

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = term.trim();
    if (!t) return;
    setState({ status: 'loading' });
    const param = /^\d+$/.test(t) ? `identifier=${encodeURIComponent(t)}` : `name=${encodeURIComponent(t)}`;
    try {
      const bundle = await fetcher<Bundle<Patient>>(`/Patient?${param}`);
      const today = todayYmd();
      setState({ status: 'ready', rows: entries(bundle).map((p) => mapPatientHeader(p, today)) });
    } catch (error) {
      setState({ status: 'error', error });
    }
  };

  return (
    <div className="container-fluid mt-3" data-section="finder">
      <section className="card">
        <div className="card-body p-2">
          <h6 className="card-title font-weight-bolder">Find a patient</h6>
          <form className="form-inline" onSubmit={search}>
            <label className="sr-only" htmlFor="finder-term">
              Name or MRN
            </label>
            <input id="finder-term" className="form-control form-control-sm mr-2" placeholder="Name or MRN" value={term} onChange={(e) => setTerm(e.target.value)} />
            <button className="btn btn-primary btn-sm" type="submit">
              Search
            </button>
          </form>
          {state.status === 'loading' && (
            <div className="spinner-border spinner-border-sm mt-2" role="status">
              <span className="sr-only">Loading...</span>
            </div>
          )}
          {state.status === 'error' && (
            <div className="alert alert-danger p-1 mt-2" role="alert">
              {errorMessage(state.error)}
            </div>
          )}
          {state.status === 'ready' &&
            (state.rows.length === 0 ? (
              <p className="mt-2 mb-0">No matching patients.</p>
            ) : (
              <table className="table table-sm table-striped mt-2 mb-0">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>MRN</th>
                    <th>DOB</th>
                    <th>Sex</th>
                  </tr>
                </thead>
                <tbody>
                  {state.rows.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <a
                          href={`/patient/${encodeURIComponent(r.id)}`}
                          onClick={(e) => {
                            e.preventDefault();
                            onOpen(r.id);
                          }}
                        >
                          {r.name}
                        </a>
                      </td>
                      <td>{r.mrn}</td>
                      <td>{r.dob}</td>
                      <td>{r.sex}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
        </div>
      </section>
    </div>
  );
}
