/**
 * SpoolBuddy Inventory: "By location" grouping of the spool grid.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { SpoolBuddyInventoryPage } from '../../pages/spoolbuddy/SpoolBuddyInventoryPage';
import { ToastProvider } from '../../contexts/ToastContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string | Record<string, unknown>) => (typeof fallback === 'string' ? fallback : key),
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

const spool = (id: number, material: string, color: string, location_id: number | null, storage_location: string | null) => ({
  id, material, subtype: null, brand: 'Bambu', color_name: color, rgba: 'FFFF00FF', tag_uid: null, tray_uuid: null,
  label_weight: 1000, weight_used: 100, core_weight: 250, archived_at: null,
  updated_at: `2026-01-${String(10 + id).padStart(2, '0')}T00:00:00Z`,
  location_id, storage_location,
});

vi.mock('../../api/client', () => ({
  api: {
    getSpoolmanSettings: vi.fn().mockResolvedValue({ spoolman_enabled: 'false', spoolman_url: '' }),
    getSpools: vi.fn(),
    getSpoolmanInventorySpools: vi.fn().mockResolvedValue([]),
    getAssignments: vi.fn().mockResolvedValue([]),
    getSpoolmanSlotAssignments: vi.fn().mockResolvedValue([]),
    getLocations: vi.fn(),
  },
}));

import { api } from '../../api/client';

const ctx = {
  selectedPrinterId: null,
  sbState: { weight: null, deviceOnline: true },
};

function renderPage() {
  function Wrapper() {
    return <Outlet context={ctx} />;
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <ToastProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/spoolbuddy/inventory']}>
          <Routes>
            <Route element={<Wrapper />}>
              <Route path="spoolbuddy/inventory" element={<SpoolBuddyInventoryPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ToastProvider>,
  );
}

const groupNames = () => screen.getAllByRole('region').map((r) => r.getAttribute('aria-label'));

describe('SpoolBuddyInventoryPage — group by location', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // setup.ts stubs localStorage with mocks; nothing remembered by default.
    vi.mocked(localStorage.getItem).mockReturnValue(null);
    vi.mocked(api.getSpools).mockResolvedValue([
      spool(1, 'PLA', 'Yellow', 3, 'BOX-01'),
      spool(2, 'PETG', 'Black', 3, 'BOX-01'),
      spool(3, 'PLA', 'Red', null, 'shelf a'),
      spool(4, 'PLA', 'White', null, null),
    ] as never);
    vi.mocked(api.getLocations).mockResolvedValue([
      { id: 3, name: 'BOX-01', identifier: null, spool_count: 2, created_at: '', updated_at: '' },
      { id: 4, name: 'Shelf A', identifier: null, spool_count: 0, created_at: '', updated_at: '' },
    ] as never);
  });

  it('shows a flat grid until the toggle is pressed', async () => {
    renderPage();
    await screen.findByText('Yellow');
    expect(screen.queryAllByRole('region')).toHaveLength(0);
    expect(screen.getByRole('button', { name: /spoolbuddy.inventory.groupByLocation/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('groups spools into one section per location with counts and "no location" last', async () => {
    renderPage();
    await screen.findByText('Yellow');
    fireEvent.click(screen.getByRole('button', { name: /spoolbuddy.inventory.groupByLocation/ }));

    await waitFor(() => expect(groupNames()).toEqual(['BOX-01', 'Shelf A', 'spoolbuddy.location.none']));
    const box = screen.getByRole('region', { name: 'BOX-01' });
    expect(within(box).getByText('Yellow')).toBeDefined();
    expect(within(box).getByText('Black')).toBeDefined();
    expect(within(box).getByRole('heading').textContent).toContain('2');
    // Legacy free-text location matched to the location of that name.
    expect(within(screen.getByRole('region', { name: 'Shelf A' })).getByText('Red')).toBeDefined();
    expect(within(screen.getByRole('region', { name: 'spoolbuddy.location.none' })).getByText('White')).toBeDefined();
    expect(localStorage.setItem).toHaveBeenCalledWith('spoolbuddy.inventory.groupByLocation', '1');
  });

  it('keeps material filter and search working inside the groups', async () => {
    vi.mocked(localStorage.getItem).mockImplementation((k: string) => (k === 'spoolbuddy.inventory.groupByLocation' ? '1' : null));
    renderPage();
    await waitFor(() => expect(groupNames()).toHaveLength(3));

    fireEvent.click(screen.getByRole('button', { name: 'PETG' }));
    await waitFor(() => expect(groupNames()).toEqual(['BOX-01']));
    expect(screen.queryByText('Yellow')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'PETG' }));
    fireEvent.change(screen.getByPlaceholderText('Search spools...'), { target: { value: 'white' } });
    await waitFor(() => expect(groupNames()).toEqual(['spoolbuddy.location.none']));
  });
});
