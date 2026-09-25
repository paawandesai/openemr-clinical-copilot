import { describe, expect, it } from 'vitest';

// THROWAWAY (work order 33, D61): proves the dashboard-tests CI job goes red
// on a failing unit test. This branch must never be merged.
describe('dashboard-tests CI job', () => {
  it('fails on purpose', () => {
    expect(1 + 1).toBe(3);
  });
});
