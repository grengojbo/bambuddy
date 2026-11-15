/**
 * A sign-in from the login page a protected route sent you to lands back on
 * that route, with the real router (not a mocked navigate), so the
 * "already signed in, leave /login" guard runs too.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { createTestQueryClient } from '../utils';
import { AuthProvider } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { ProtectedRoute } from '../../App';
import { LoginPage } from '../../pages/LoginPage';
import { setAuthToken } from '../../api/client';

const mockUser = {
  id: 1,
  username: 'kiosk',
  role: 'admin' as const,
  is_active: true,
  created_at: new Date().toISOString(),
};

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname}</div>;
}

function renderAt(path: string) {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <ThemeProvider>
            <ToastProvider>
              <Routes>
                <Route path="/login" element={<LoginPage />} />
                <Route path="/" element={<ProtectedRoute><div>home</div></ProtectedRoute>} />
                <Route path="/spoolbuddy" element={<ProtectedRoute><div>spoolbuddy</div></ProtectedRoute>} />
              </Routes>
              <Where />
            </ToastProvider>
          </ThemeProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('sign-in returns to the page that required it', () => {
  let signedIn = false;

  beforeEach(() => {
    signedIn = false;
    server.use(
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('/api/v1/auth/me', () =>
        signedIn ? HttpResponse.json(mockUser) : HttpResponse.json({ detail: 'Token has expired' }, { status: 401 }),
      ),
      http.post('/api/v1/auth/login', () => {
        signedIn = true;
        return HttpResponse.json({ access_token: 'fresh', token_type: 'bearer', user: mockUser });
      }),
    );
  });

  afterEach(() => {
    setAuthToken(null);
    sessionStorage.clear();
  });

  it('lands on /spoolbuddy after an expired session sent it to /login', async () => {
    setAuthToken('expired', 'persistent');
    const user = userEvent.setup();
    renderAt('/spoolbuddy');

    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/login'));
    await user.type(await screen.findByLabelText(/Username/i), 'kiosk');
    await user.type(screen.getByLabelText(/Password/i), 'secret');
    await user.click(screen.getByRole('button', { name: /Sign in/i }));

    await waitFor(() => expect(screen.getByText('spoolbuddy')).toBeInTheDocument());
    // Nothing may move it on afterwards.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByTestId('where')).toHaveTextContent('/spoolbuddy');
  });

  it('lands on / when /login was opened directly', async () => {
    const user = userEvent.setup();
    renderAt('/login');

    await user.type(await screen.findByLabelText(/Username/i), 'kiosk');
    await user.type(screen.getByLabelText(/Password/i), 'secret');
    await user.click(screen.getByRole('button', { name: /Sign in/i }));

    await waitFor(() => expect(screen.getByText('home')).toBeInTheDocument());
    expect(screen.getByTestId('where').textContent).toBe('/');
  });
});
