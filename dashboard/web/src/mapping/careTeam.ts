import type { CareTeam, Organization, Practitioner } from '../fhir/types';
import { conceptText, ymd } from './format';

export interface CareTeamMemberView {
  key: string;
  /** "Provider" for a Practitioner, "Related Person" for a RelatedPerson, as the PHP card's badge. */
  typeLabel: string;
  typeBadge: 'badge-primary' | 'badge-info';
  /** e.g. `Practitioner/<uuid>`; resolved to a name separately. */
  memberRef: string | null;
  role: string;
  facilityRef: string | null;
  since: string;
}

export interface CareTeamView {
  id: string;
  name: string;
  status: string;
  statusLabel: string;
  statusBadge: string;
  members: CareTeamMemberView[];
}

/** CareTeamViewCard.php's badge map. */
const BADGE: Record<string, string> = {
  active: 'badge-success',
  inactive: 'badge-warning',
  proposed: 'badge-info',
  'entered-in-error': 'badge-danger',
};

function titleCase(s: string): string {
  return s
    .split('-')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * OpenEMR emits each member as a participant, plus one participant per
 * facility whose member *is* the Organization. The PHP table shows the
 * facility in a column of the member's row, so the facility-only entries are
 * dropped here and the member's `onBehalfOf` supplies the column.
 * Member status and note are not emitted by FHIR (inventory G10).
 */
export function mapCareTeams(list: CareTeam[]): CareTeamView[] {
  return list.map((t) => {
    const status = t.status ?? '';
    return {
      id: t.id ?? '',
      name: t.name ?? '',
      status,
      statusLabel: status ? titleCase(status) : '',
      statusBadge: BADGE[status] ?? 'badge-secondary',
      members: (t.participant ?? [])
        .filter((p) => {
          const type = p.member?.type ?? p.member?.reference?.split('/')[0];
          return type === 'Practitioner' || type === 'RelatedPerson';
        })
        .map((p, i) => {
          const type = p.member?.type ?? p.member?.reference?.split('/')[0];
          const related = type === 'RelatedPerson';
          return {
            key: `${p.member?.reference ?? 'member'}#${i}`,
            typeLabel: related ? 'Related Person' : 'Provider',
            typeBadge: related ? 'badge-info' : 'badge-primary',
            memberRef: p.member?.reference ?? null,
            role: p.role?.map((r) => conceptText(r)).find(Boolean) ?? '',
            facilityRef: p.onBehalfOf?.reference ?? null,
            since: ymd(p.period?.start),
          };
        }),
    };
  });
}

/** Every Practitioner / Organization the teams reference, deduplicated, for one parallel batch of reads. */
export function careTeamReferences(teams: CareTeamView[]): string[] {
  const refs = new Set<string>();
  for (const t of teams) {
    for (const m of t.members) {
      if (m.memberRef?.startsWith('Practitioner/')) refs.add(m.memberRef);
      if (m.facilityRef?.startsWith('Organization/')) refs.add(m.facilityRef);
    }
  }
  return [...refs];
}

/** "Smith, Billy", the PHP card's `user_display` format. */
export function practitionerName(p: Practitioner): string {
  const n = p.name?.find((x) => x.use === 'official') ?? p.name?.[0];
  if (!n) return '';
  const given = (n.given ?? []).join(' ');
  return [n.family, given].filter(Boolean).join(', ') || n.text || '';
}

export function organizationName(o: Organization): string {
  return o.name ?? '';
}

/** A reference's display state: a name, or why there is none. */
export type ResolvedName = { kind: 'name'; text: string } | { kind: 'restricted' } | { kind: 'unavailable' } | { kind: 'loading' };
