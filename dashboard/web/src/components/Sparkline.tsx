import type { LabTrend } from '../mapping/labs';

const W = 96;
const H = 22;
const PAD = 3;

/**
 * A per-analyte trend: the values over time, with the reference range as a
 * band when OpenEMR sent one. Points outside the range are drawn in the
 * danger colour. The exact values and dates are in the accessible title.
 */
export function Sparkline({ trend }: { trend: LabTrend }) {
  const values = trend.points.map((p) => p.value);
  const lo = Math.min(...values, trend.low ?? Infinity);
  const hi = Math.max(...values, trend.high ?? -Infinity);
  const span = hi - lo || 1;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / Math.max(1, trend.points.length - 1);
  const y = (v: number) => H - PAD - ((v - lo) * (H - 2 * PAD)) / span;
  const out = (v: number) => (trend.low !== null && v < trend.low) || (trend.high !== null && v > trend.high);
  const label = `${trend.test} trend: ${trend.points.map((p) => `${p.date} ${p.value}${trend.unit ? ' ' + trend.unit : ''}`).join('; ')}`;
  return (
    <svg className="lab-trend" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      <title>{label}</title>
      {trend.low !== null && trend.high !== null && (
        <rect className="band" x={0} width={W} y={y(trend.high)} height={Math.max(1, y(trend.low) - y(trend.high))} />
      )}
      <polyline className="line" points={trend.points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ')} />
      {trend.points.map((p, i) => (
        <circle key={i} className={`dot${out(p.value) ? ' out' : ''}`} cx={x(i).toFixed(1)} cy={y(p.value).toFixed(1)} r={1.8} />
      ))}
    </svg>
  );
}
