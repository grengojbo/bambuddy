import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, MapPin, Pencil, X } from 'lucide-react';
import { api } from '../../api/client';
import { inventoryLocationsQueryKey, invalidateInventoryLocations } from '../../utils/inventoryQueries';
import { resolveSpoolLocation } from '../../utils/spoolLocation';

interface SpoolLocationFieldProps {
  spoolId: number;
  locationId?: number | null;
  storageLocation?: string | null;
  spoolmanMode: boolean;
}

/**
 * The spool's storage location as a row of the kiosk spool card, with a
 * touch picker to move it. Writes `location_id` through the same calls the
 * "Assign by QR" storage tab uses, so the legacy text follows on the backend.
 */
export function SpoolLocationField({ spoolId, locationId, storageLocation, spoolmanMode }: SpoolLocationFieldProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: locations = [], isLoading } = useQuery({
    queryKey: inventoryLocationsQueryKey,
    queryFn: api.getLocations,
  });

  const current = resolveSpoolLocation({ location_id: locationId, storage_location: storageLocation }, locations);

  const mutation = useMutation({
    mutationFn: async (newLocationId: number | null) => {
      if (spoolmanMode) {
        await api.updateSpoolmanInventorySpool(spoolId, { location_id: newLocationId });
      } else {
        await api.updateSpool(spoolId, { location_id: newLocationId });
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: spoolmanMode ? ['spoolman-inventory-spools'] : ['inventory-spools'] });
      // Per-location spool_count changes with the move.
      void invalidateInventoryLocations(queryClient);
      setPickerOpen(false);
    },
    onError: (e: Error) => {
      // Shown in the picker, which stays open: the kiosk shows no toasts.
      setError(`${t('spoolbuddy.location.saveFailed')}: ${e.message}`);
    },
  });

  const openPicker = () => {
    setError(null);
    setPickerOpen(true);
  };

  const pick = (id: number | null) => {
    if (mutation.isPending) return;
    if (id === current.id && (id !== null || !current.name)) {
      setPickerOpen(false);
      return;
    }
    setError(null);
    mutation.mutate(id);
  };

  return (
    <>
      <button
        type="button"
        onClick={openPicker}
        aria-label={t('spoolbuddy.location.change')}
        className="col-span-2 -mx-2 px-2 min-h-[44px] flex items-center justify-between gap-3 rounded-lg hover:bg-zinc-700/60 active:bg-zinc-700 transition-colors text-left"
      >
        <span className="flex items-center gap-1.5 text-zinc-500 shrink-0">
          <MapPin className="w-4 h-4" />
          {t('spoolbuddy.location.label')}
        </span>
        <span className="flex items-center gap-2 min-w-0">
          <span className={`truncate ${current.name ? 'text-zinc-200 font-medium' : 'text-zinc-500'}`} data-testid="spool-location-value">
            {current.name ?? t('spoolbuddy.location.none')}
          </span>
          <Pencil className="w-4 h-4 text-green-500 shrink-0" />
        </span>
      </button>

      {pickerOpen && (
        <div
          className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4"
          onClick={() => !mutation.isPending && setPickerOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t('spoolbuddy.location.label')}
            className="w-full max-w-2xl max-h-[85vh] flex flex-col bg-zinc-900 border border-zinc-700 rounded-2xl p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3 shrink-0">
              <h2 className="text-lg font-semibold text-zinc-100 flex items-center gap-2">
                <MapPin className="w-5 h-5 text-green-500" />
                {t('spoolbuddy.location.label')}
              </h2>
              <button
                type="button"
                onClick={() => setPickerOpen(false)}
                disabled={mutation.isPending}
                aria-label={t('spoolbuddy.dashboard.close', 'Close')}
                className="w-11 h-11 flex items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-800 disabled:opacity-40"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {error && (
              <p role="alert" className="mb-3 px-3 py-2 rounded-lg bg-red-500/15 text-red-400 text-sm shrink-0">
                {error}
              </p>
            )}

            <div className="overflow-y-auto min-h-0">
              {isLoading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
                </div>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {locations.map((loc) => {
                    const selected = current.id === loc.id;
                    const pending = mutation.isPending && mutation.variables === loc.id;
                    return (
                      <button
                        key={loc.id}
                        type="button"
                        onClick={() => pick(loc.id)}
                        disabled={mutation.isPending}
                        aria-pressed={selected}
                        className={`min-h-[56px] px-3 py-2 rounded-xl border text-left flex items-center justify-between gap-2 transition-colors disabled:opacity-60 ${
                          selected
                            ? 'bg-green-600/20 border-green-500 text-green-400'
                            : 'bg-zinc-800 border-zinc-700 text-zinc-200 hover:border-zinc-500'
                        }`}
                      >
                        <span className="min-w-0">
                          <span className="block font-medium truncate">{loc.name}</span>
                          {loc.identifier && <span className="block text-xs text-zinc-500 font-mono truncate">{loc.identifier}</span>}
                        </span>
                        {pending ? (
                          <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                        ) : selected ? (
                          <Check className="w-4 h-4 shrink-0" />
                        ) : (
                          <span className="text-xs text-zinc-500 shrink-0">{loc.spool_count}</span>
                        )}
                      </button>
                    );
                  })}
                  <button
                    type="button"
                    onClick={() => pick(null)}
                    disabled={mutation.isPending}
                    aria-pressed={!current.name}
                    className={`min-h-[56px] px-3 py-2 rounded-xl border border-dashed text-left flex items-center justify-between gap-2 transition-colors disabled:opacity-60 ${
                      !current.name ? 'border-green-500 text-green-400' : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
                    }`}
                  >
                    <span className="font-medium">{t('spoolbuddy.location.none')}</span>
                    {mutation.isPending && mutation.variables === null ? (
                      <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                    ) : !current.name ? (
                      <Check className="w-4 h-4 shrink-0" />
                    ) : null}
                  </button>
                </div>
              )}
              {!isLoading && locations.length === 0 && (
                <p className="mt-3 text-sm text-zinc-500">{t('spoolbuddy.location.noLocations')}</p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
