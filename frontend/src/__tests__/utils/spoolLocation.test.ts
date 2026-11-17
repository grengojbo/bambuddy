import { describe, it, expect } from 'vitest';
import type { StorageLocation } from '../../api/client';
import { groupSpoolsByLocation, resolveSpoolLocation } from '../../utils/spoolLocation';

const loc = (id: number, name: string): StorageLocation => ({
  id, name, identifier: null, spool_count: 0, created_at: '', updated_at: '',
});

const LOCATIONS = [loc(3, 'BOX-01'), loc(5, 'Shelf 2'), loc(7, 'Shelf 10')];

describe('resolveSpoolLocation', () => {
  it('uses the location row named by location_id', () => {
    expect(resolveSpoolLocation({ location_id: 3, storage_location: 'stale text' }, LOCATIONS)).toEqual({ id: 3, name: 'BOX-01' });
  });

  it('falls back to the legacy text while the locations list is not loaded', () => {
    expect(resolveSpoolLocation({ location_id: 3, storage_location: 'BOX-01' }, [])).toEqual({ id: 3, name: 'BOX-01' });
  });

  it('matches legacy text to a location by name, ignoring case and spaces', () => {
    expect(resolveSpoolLocation({ location_id: null, storage_location: '  shelf 2 ' }, LOCATIONS)).toEqual({ id: 5, name: 'Shelf 2' });
  });

  it('keeps unmatched legacy text as the name', () => {
    expect(resolveSpoolLocation({ storage_location: 'Garage' }, LOCATIONS)).toEqual({ id: null, name: 'Garage' });
  });

  it('reports no location for empty fields', () => {
    expect(resolveSpoolLocation({ location_id: null, storage_location: '  ' }, LOCATIONS)).toEqual({ id: null, name: null });
    expect(resolveSpoolLocation({}, LOCATIONS)).toEqual({ id: null, name: null });
  });
});

describe('groupSpoolsByLocation', () => {
  it('groups by resolved location, sorts naturally, puts "no location" last and keeps spool order', () => {
    const spools = [
      { id: 1, location_id: null, storage_location: null },
      { id: 2, location_id: 7, storage_location: 'Shelf 10' },
      { id: 3, location_id: 3, storage_location: 'BOX-01' },
      { id: 4, location_id: null, storage_location: 'shelf 2' },
      { id: 5, location_id: 5, storage_location: 'Shelf 2' },
      { id: 6, location_id: null, storage_location: 'Garage' },
      { id: 7, location_id: 3, storage_location: 'BOX-01' },
    ];
    const groups = groupSpoolsByLocation(spools, LOCATIONS);
    expect(groups.map((g) => [g.key, g.name, g.spools.map((s) => s.id)])).toEqual([
      ['id:3', 'BOX-01', [3, 7]],
      ['text:garage', 'Garage', [6]],
      ['id:5', 'Shelf 2', [4, 5]],
      ['id:7', 'Shelf 10', [2]],
      ['none', null, [1]],
    ]);
  });

  it('returns no groups for no spools', () => {
    expect(groupSpoolsByLocation([], LOCATIONS)).toEqual([]);
  });
});
