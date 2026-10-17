/** Only download the assistant chunk when an actual service is available. */
import { getFeatures } from './api';
import { isSaaS } from './brand-config';
export async function installSupportAssistant(): Promise<void> {
  const path = window.location.pathname;
  if (!(
    path.startsWith('/console') ||
    (isSaaS() && ['/', '/pricing', '/register'].includes(path))
  )) {
    document.querySelector('support-assistant')?.remove();
    return;
  }
  try {
    if ((await getFeatures()).features.cloud_support !== true) return;
    await import('./components/support-assistant');
    if (!document.querySelector('support-assistant'))
      document.body.append(document.createElement('support-assistant'));
  } catch {
    /* Optional assistance stays hidden when unavailable. */
  }
}
