import { fetchWithAuth } from '../api';

/** Generic plugin contract; processing and wording belong to the provider. */
export interface ExtensionAttentionItem {
  id: string;
  source: string;
  title: string;
  detail: string;
  href: string;
  severity: 'info' | 'action';
  opened_at: string;
}

export async function loadExtensionAttentionItems(): Promise<
  ExtensionAttentionItem[]
> {
  try {
    const response = await fetchWithAuth('/api/v1/attention/extension-items');
    if (!response.ok) return [];
    const items: unknown = await response.json();
    return Array.isArray(items) ? items : [];
  } catch {
    return [];
  }
}
