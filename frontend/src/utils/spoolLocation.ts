import type { InventorySpool, StorageLocation } from '../api/client';

type SpoolLocationFields = Pick<InventorySpool, 'location_id' | 'storage_location'>;

export interface ResolvedSpoolLocation {
  /** Structured location row, when the spool points at one (or its legacy text names one). */
  id: number | null;
  /** What to show; null when the spool has no location at all. */
  name: string | null;
}

const nameKey = (name: string) => name.trim().toLowerCase();

/**
 * Where a spool is stored, by the rule the main inventory filter uses (#1400):
 * `location_id` wins; a spool with only the legacy free-text
 * `storage_location` belongs to the location of that name, if one exists,
 * and otherwise keeps its text as-is.
 */
export function resolveSpoolLocation(
  spool: SpoolLocationFields,
  locations: readonly StorageLocation[],
): ResolvedSpoolLocation {
  if (spool.location_id != null) {
    const loc = locations.find((l) => l.id === spool.location_id);
    return { id: spool.location_id, name: loc?.name ?? spool.storage_location?.trim() ?? null };
  }
  const text = spool.storage_location?.trim();
  if (!text) return { id: null, name: null };
  const loc = locations.find((l) => nameKey(l.name) === nameKey(text));
  return loc ? { id: loc.id, name: loc.name } : { id: null, name: text };
}

export interface SpoolLocationGroup<T> {
  /** Stable React key: `id:<n>`, `text:<name>` or `none`. */
  key: string;
  locationId: number | null;
  /** Null for the "no location" group. */
  name: string | null;
  spools: T[];
}

/**
 * Splits spools into one group per storage location, keeping each group's
 * spools in the order given. Groups are sorted by name; spools without a
 * location come last.
 */
export function groupSpoolsByLocation<T extends SpoolLocationFields>(
  spools: readonly T[],
  locations: readonly StorageLocation[],
): SpoolLocationGroup<T>[] {
  const groups = new Map<string, SpoolLocationGroup<T>>();
  for (const spool of spools) {
    const { id, name } = resolveSpoolLocation(spool, locations);
    const key = id != null ? `id:${id}` : name ? `text:${nameKey(name)}` : 'none';
    let group = groups.get(key);
    if (!group) {
      group = { key, locationId: id, name: name ?? (id != null ? `#${id}` : null), spools: [] };
      groups.set(key, group);
    }
    group.spools.push(spool);
  }
  return [...groups.values()].sort((a, b) => {
    if (a.name === null) return 1;
    if (b.name === null) return -1;
    return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
  });
}
