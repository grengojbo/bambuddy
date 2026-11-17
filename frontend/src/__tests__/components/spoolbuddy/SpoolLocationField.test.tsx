import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SpoolLocationField } from '../../../components/spoolbuddy/SpoolLocationField';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

vi.mock('../../../api/client', () => ({
  api: {
    getLocations: vi.fn(),
    updateSpool: vi.fn(),
    updateSpoolmanInventorySpool: vi.fn(),
  },
}));

import { api } from '../../../api/client';

const LOCATIONS = [
  { id: 3, name: 'BOX-01', identifier: 'B1', spool_count: 4, created_at: '', updated_at: '' },
  { id: 5, name: 'Shelf 2', identifier: null, spool_count: 1, created_at: '', updated_at: '' },
];

function renderField(props: Partial<React.ComponentProps<typeof SpoolLocationField>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <div className="grid grid-cols-2">
        <SpoolLocationField spoolId={37} locationId={3} storageLocation="BOX-01" spoolmanMode={false} {...props} />
      </div>
    </QueryClientProvider>,
  );
  return { invalidate };
}

describe('SpoolLocationField', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getLocations).mockResolvedValue(LOCATIONS as never);
    vi.mocked(api.updateSpool).mockResolvedValue({} as never);
    vi.mocked(api.updateSpoolmanInventorySpool).mockResolvedValue({} as never);
  });

  it('shows the current location by location_id', async () => {
    renderField();
    await waitFor(() => expect(screen.getByTestId('spool-location-value').textContent).toBe('BOX-01'));
  });

  it('shows "no location" when the spool has none', async () => {
    renderField({ locationId: null, storageLocation: null });
    expect(screen.getByTestId('spool-location-value').textContent).toBe('spoolbuddy.location.none');
  });

  it('moves the spool to the picked location and refreshes spools and locations', async () => {
    const { invalidate } = renderField();
    fireEvent.click(screen.getByRole('button', { name: 'spoolbuddy.location.change' }));
    fireEvent.click(await screen.findByRole('button', { name: /Shelf 2/ }));
    await waitFor(() => expect(api.updateSpool).toHaveBeenCalledWith(37, { location_id: 5 }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['inventory-spools'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['inventory-locations'] });
    expect(api.updateSpoolmanInventorySpool).not.toHaveBeenCalled();
  });

  it('clears the location with the "no location" choice', async () => {
    renderField();
    fireEvent.click(screen.getByRole('button', { name: 'spoolbuddy.location.change' }));
    await screen.findByRole('button', { name: /Shelf 2/ });
    fireEvent.click(screen.getByRole('button', { name: 'spoolbuddy.location.none' }));
    await waitFor(() => expect(api.updateSpool).toHaveBeenCalledWith(37, { location_id: null }));
  });

  it('writes through the Spoolman route in Spoolman mode', async () => {
    const { invalidate } = renderField({ spoolmanMode: true });
    fireEvent.click(screen.getByRole('button', { name: 'spoolbuddy.location.change' }));
    fireEvent.click(await screen.findByRole('button', { name: /Shelf 2/ }));
    await waitFor(() => expect(api.updateSpoolmanInventorySpool).toHaveBeenCalledWith(37, { location_id: 5 }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['spoolman-inventory-spools'] });
    expect(api.updateSpool).not.toHaveBeenCalled();
  });

  it('picking the current location just closes the picker', async () => {
    renderField();
    fireEvent.click(screen.getByRole('button', { name: 'spoolbuddy.location.change' }));
    fireEvent.click(await screen.findByRole('button', { name: /BOX-01/ }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.updateSpool).not.toHaveBeenCalled();
  });

  it('keeps the picker open with the error when saving fails', async () => {
    vi.mocked(api.updateSpool).mockRejectedValueOnce(new Error('boom'));
    renderField();
    fireEvent.click(screen.getByRole('button', { name: 'spoolbuddy.location.change' }));
    fireEvent.click(await screen.findByRole('button', { name: /Shelf 2/ }));
    expect((await screen.findByRole('alert')).textContent).toBe('spoolbuddy.location.saveFailed: boom');
    expect(screen.getByRole('dialog')).toBeDefined();
  });
});
