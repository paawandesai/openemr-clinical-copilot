import { useState, type ReactNode } from 'react';
import { errorMessage } from '../api';
import type { AsyncState } from '../useAsync';

function readCollapsed(id: string): boolean {
  try {
    return window.localStorage.getItem(`dashboard.card.${id}`) === 'collapsed';
  } catch {
    return false;
  }
}

function writeCollapsed(id: string, collapsed: boolean): void {
  try {
    window.localStorage.setItem(`dashboard.card.${id}`, collapsed ? 'collapsed' : 'open');
  } catch {
    /* storage blocked: the card still works, it just forgets */
  }
}

interface CardProps<T> {
  /** The PHP card's element id (`allergy_ps_expand`), kept so the two pages can be compared by id. */
  id: string;
  title: string;
  state: AsyncState<T>;
  containerClass?: string;
  children: (data: T) => ReactNode;
}

/**
 * templates/patient/card/card_base.html.twig in React: the same markup and
 * classes, a title that toggles collapse with the same icons, and the
 * loader.html.twig spinner while loading. What the PHP page does not have
 * is the error state: the PHP page renders every card in one request, so a
 * card cannot fail alone. Here each card reads separately, and a failed
 * read is shown in its own card while the rest of the page carries on.
 * Collapse state is remembered per browser (PHP keeps it per user; G13).
 */
export function Card<T>({ id, title, state, containerClass = 'flex-fill mx-1 card', children }: CardProps<T>) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(id));
  const toggle = (e: React.MouseEvent) => {
    e.preventDefault();
    setCollapsed((c) => {
      writeCollapsed(id, !c);
      return !c;
    });
  };
  return (
    <section className={containerClass} data-card={id} aria-busy={state.status === 'loading'}>
      <div className="card-body p-1">
        <h6 className="card-title mb-0 d-flex p-1 justify-content-between">
          <a className="text-left font-weight-bolder" href="#" onClick={toggle} aria-expanded={!collapsed} aria-controls={id}>
            {title}
            <i className={`ml-1 fa fa-fw ${collapsed ? 'fa-expand' : 'fa-compress'}`} aria-hidden="true"></i>
          </a>
        </h6>
        <div id={id} className={`card-text collapse${collapsed ? '' : ' show'}`}>
          <div className="clearfix pt-2">
            {state.status === 'loading' && (
              <div className="text ml-2" data-state="loading">
                <div className="spinner-border spinner-border-sm" role="status">
                  <span className="sr-only">Loading...</span>
                </div>
              </div>
            )}
            {state.status === 'error' && (
              <div className="alert alert-danger p-1 m-1 mb-0" role="alert" data-state="error">
                {errorMessage(state.error)}
              </div>
            )}
            {state.status === 'ready' && children(state.data)}
          </div>
        </div>
      </div>
    </section>
  );
}

/** The PHP cards' empty row: `list-group-item p-0 pl-1`. */
export function EmptyRow({ text }: { text: string }) {
  return (
    <div className="list-group-item p-0 pl-1" data-state="empty">
      {text}
    </div>
  );
}
