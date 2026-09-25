import { useCallback, useEffect, useState } from 'react';
import { fhirGet, getSession, logout, SESSION_ENDED, type Fetcher } from './api';
import { Dashboard } from './components/Dashboard';
import { PatientFinder } from './components/PatientFinder';

type Auth = 'checking' | 'signed-in' | 'signed-out' | 'ended';

const LOGIN_ERRORS: Record<string, string> = {
  access_denied: 'Sign-in was cancelled or refused in OpenEMR.',
  login_expired: 'The sign-in took too long. Try again.',
  state_mismatch: 'The sign-in response did not match this browser. Try again.',
};

function patientFromPath(path: string): string | null {
  const m = /^\/patient\/([A-Za-z0-9\-.]{1,64})$/.exec(path);
  return m ? m[1]! : null;
}

export function App({ fetcher = fhirGet }: { fetcher?: Fetcher }) {
  const [auth, setAuth] = useState<Auth>('checking');
  const [path, setPath] = useState(() => window.location.pathname);
  const loginError = new URLSearchParams(window.location.search).get('login_error');

  useEffect(() => {
    getSession().then(
      (s) => setAuth(s.authenticated ? 'signed-in' : 'signed-out'),
      () => setAuth('signed-out'),
    );
    const ended = () => setAuth('ended');
    const pop = () => setPath(window.location.pathname);
    window.addEventListener(SESSION_ENDED, ended);
    window.addEventListener('popstate', pop);
    return () => {
      window.removeEventListener(SESSION_ENDED, ended);
      window.removeEventListener('popstate', pop);
    };
  }, []);

  const open = useCallback((id: string) => {
    const next = `/patient/${encodeURIComponent(id)}`;
    window.history.pushState(null, '', next);
    setPath(next);
  }, []);

  const signOut = async () => {
    const endSession = await logout().catch(() => null);
    // OpenEMR's sign-out ends its own session and redirects back here.
    window.location.assign(endSession ?? '/');
  };

  const patientId = patientFromPath(path);
  const loginHref = `/auth/login?return=${encodeURIComponent(path)}`;

  return (
    <>
      <nav className="navbar navbar-light bg-white app-bar px-3 py-1">
        <a
          className="navbar-brand h6 mb-0"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            window.history.pushState(null, '', '/');
            setPath('/');
          }}
        >
          Patient Dashboard
        </a>
        {auth === 'signed-in' && (
          <button type="button" className="btn btn-link btn-sm" onClick={signOut}>
            Sign out
          </button>
        )}
      </nav>
      {auth === 'checking' && (
        <div className="m-3 spinner-border spinner-border-sm" role="status">
          <span className="sr-only">Loading...</span>
        </div>
      )}
      {(auth === 'signed-out' || auth === 'ended') && (
        <div className="container mt-5" data-section="login">
          <section className="card mx-auto">
            <div className="card-body">
              <h1 className="h5">Patient Dashboard</h1>
              {auth === 'ended' && <div className="alert alert-warning p-2">Your session has ended. Sign in again to continue.</div>}
              {loginError && <div className="alert alert-danger p-2">{LOGIN_ERRORS[loginError] ?? 'Sign-in failed. Try again.'}</div>}
              <p>Sign in with your OpenEMR account. This dashboard reads the chart with your own OpenEMR permissions and cannot change it.</p>
              <a className="btn btn-primary" href={loginHref}>
                Sign in with OpenEMR
              </a>
            </div>
          </section>
        </div>
      )}
      {auth === 'signed-in' && (patientId ? <Dashboard key={patientId} patientId={patientId} fetcher={fetcher} /> : <PatientFinder fetcher={fetcher} onOpen={open} />)}
    </>
  );
}
