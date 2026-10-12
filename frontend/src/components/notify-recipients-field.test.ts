import { html, fixture, expect } from '@open-wc/testing';
import './notify-recipients-field.ts';
import type { User } from '../types';
import type { NotifyRecipientsField } from './notify-recipients-field';

describe('NotifyRecipientsField', () => {
  async function addCustomEmail(element: NotifyRecipientsField, value: string) {
    const input = element.shadowRoot?.querySelector('sl-input');
    expect(input).to.exist;
    (input as HTMLInputElement).value = value;
    input?.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    const addButton = [
      ...(element.shadowRoot?.querySelectorAll('sl-button') || []),
    ].find((button) => button.textContent?.trim() === 'Add');
    expect(addButton).to.exist;
    addButton?.click();
    await element.updateComplete;
  }

  it('rejects invalid custom email addresses with inline feedback', async () => {
    const element = (await fixture(
      html`<notify-recipients-field></notify-recipients-field>`
    )) as NotifyRecipientsField;

    await addCustomEmail(element, 'not-an-email');

    const badges = element.shadowRoot?.querySelectorAll('sl-badge');
    expect(badges?.length || 0).to.equal(0);
    expect(element.shadowRoot?.textContent).to.contain(
      'Enter a valid email address.'
    );
  });

  it('accepts valid custom email addresses', async () => {
    const element = (await fixture(
      html`<notify-recipients-field></notify-recipients-field>`
    )) as NotifyRecipientsField;

    await addCustomEmail(element, 'alerts@example.com');

    expect(element.shadowRoot?.textContent).to.contain('alerts@example.com');
  });

  it('drops empty user slots from a select change', async () => {
    const element = (await fixture(
      html`<notify-recipients-field></notify-recipients-field>`
    )) as NotifyRecipientsField;
    const select = element.shadowRoot?.querySelector('sl-select') as
      (HTMLElement & { value: unknown }) | null;
    expect(select).to.exist;
    select!.value = ['user:', 'user:abc', '', null];
    select!.dispatchEvent(new CustomEvent('sl-change', { bubbles: true }));
    await element.updateComplete;
    expect(element.userIds).to.deep.equal(['abc']);
  });
});

it('labels unavailable identities with membership context and retains selection IDs', async () => {
  const users = [
    { id: 'membership-child', username: null, email: null },
  ] as User[];
  const element = await fixture<NotifyRecipientsField>(
    html`<notify-recipients-field .users=${users}></notify-recipients-field>`
  );
  const option = element.shadowRoot!.querySelector('sl-option')!;
  expect(option.textContent).to.contain('Identity unavailable');
  expect(option.textContent).to.contain('membership-child');
  expect(option.getAttribute('value')).to.equal('user:membership-child');
});
