import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { useSpoolBuddyManifest, SPOOLBUDDY_MANIFEST } from '../../hooks/useSpoolBuddyManifest';

const PUBLIC_DIR = resolve(__dirname, '../../../public');

function manifestLink(): HTMLLinkElement | null {
  return document.head.querySelector('link[rel="manifest"]');
}

function appleTitle(): HTMLMetaElement | null {
  return document.head.querySelector('meta[name="apple-mobile-web-app-title"]');
}

describe('useSpoolBuddyManifest', () => {
  beforeEach(() => {
    document.head.innerHTML =
      '<link rel="manifest" href="/manifest.json" />' +
      '<meta name="apple-mobile-web-app-title" content="Bambuddy" />';
  });

  it('points the page at the SpoolBuddy manifest while mounted', () => {
    renderHook(() => useSpoolBuddyManifest());
    expect(manifestLink()?.getAttribute('href')).toBe(SPOOLBUDDY_MANIFEST);
    expect(appleTitle()?.getAttribute('content')).toBe('SpoolBuddy');
  });

  it('restores the Bambuddy manifest on unmount', () => {
    const { unmount } = renderHook(() => useSpoolBuddyManifest());
    unmount();
    expect(manifestLink()?.getAttribute('href')).toBe('/manifest.json');
    expect(appleTitle()?.getAttribute('content')).toBe('Bambuddy');
  });

  it('adds a manifest link when the page has none', () => {
    document.head.innerHTML = '';
    const { unmount } = renderHook(() => useSpoolBuddyManifest());
    expect(manifestLink()?.getAttribute('href')).toBe(SPOOLBUDDY_MANIFEST);
    unmount();
    expect(manifestLink()).toBeNull();
  });
});

describe('SpoolBuddy web app manifest', () => {
  const manifest = JSON.parse(readFileSync(resolve(PUBLIC_DIR, SPOOLBUDDY_MANIFEST.slice(1)), 'utf8'));

  it('installs as its own app that opens on the SpoolBuddy dashboard', () => {
    expect(manifest.id).toBe('/spoolbuddy');
    expect(manifest.start_url).toBe('/spoolbuddy');
    expect(manifest.scope).toBe('/spoolbuddy');
    expect(manifest.name).toBe('SpoolBuddy');
  });

  it('runs full-screen like the kiosk', () => {
    expect(manifest.display).toBe('fullscreen');
  });

  it('ships installable icons that exist on disk', () => {
    const sizes = manifest.icons.map((i: { sizes: string }) => i.sizes);
    expect(sizes).toContain('192x192');
    expect(sizes).toContain('512x512');
    for (const icon of manifest.icons) {
      expect(() => readFileSync(resolve(PUBLIC_DIR, icon.src.slice(1)))).not.toThrow();
    }
  });
});
