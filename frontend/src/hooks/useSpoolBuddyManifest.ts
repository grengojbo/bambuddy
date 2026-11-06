import { useEffect } from 'react';

export const SPOOLBUDDY_MANIFEST = '/manifest-spoolbuddy.json';
const APP_TITLE = 'SpoolBuddy';

/**
 * Swap the web app manifest to SpoolBuddy's while its pages are mounted.
 *
 * index.html ships the Bambuddy manifest (start_url "/"), so installing the
 * app from /spoolbuddy on a tablet would open the main dashboard. With this
 * manifest the tablet gets a separate full-screen "SpoolBuddy" app that
 * starts on /spoolbuddy. The original values are restored on unmount.
 */
export function useSpoolBuddyManifest() {
  useEffect(() => {
    let link = document.head.querySelector<HTMLLinkElement>('link[rel="manifest"]');
    const createdLink = !link;
    if (!link) {
      link = document.createElement('link');
      link.rel = 'manifest';
      document.head.appendChild(link);
    }
    const previousHref = link.getAttribute('href');
    link.setAttribute('href', SPOOLBUDDY_MANIFEST);

    const title = document.head.querySelector<HTMLMetaElement>('meta[name="apple-mobile-web-app-title"]');
    const previousTitle = title?.getAttribute('content') ?? null;
    title?.setAttribute('content', APP_TITLE);

    return () => {
      if (createdLink) {
        link.remove();
      } else if (previousHref !== null) {
        link.setAttribute('href', previousHref);
      }
      if (title && previousTitle !== null) {
        title.setAttribute('content', previousTitle);
      }
    };
  }, []);
}
