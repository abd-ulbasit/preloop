import { expect, waitUntil } from '@open-wc/testing';
import { sendKeys, sendMouse, setViewport } from '@web/test-runner-commands';
import type { LitElement } from 'lit';
import {
  resourceAccessView,
  type ResourceAccessViewKind,
} from '../../../test-helpers/resource-access-view';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../../../test-helpers/capability-api';
import {
  resourceSummary,
  directoryRoutes,
} from '../../../test-helpers/access-policy';
import type { ResourceAccessPanel } from './resource-access-panel';
const control = (panel: ResourceAccessPanel, name: string) =>
  panel.shadowRoot!.querySelector<HTMLElement>(`[data-testid="${name}"]`)!;
/** The element a pointer at the centre of `element` would land on. */
function deepHit(element: HTMLElement): Element | null {
  const box = element.getBoundingClientRect();
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2];
  let hit = document.elementFromPoint(x, y);
  while (hit?.shadowRoot) {
    const next = hit.shadowRoot.elementFromPoint(x, y);
    if (!next || next === hit) break;
    hit = next;
  }
  return hit;
}
async function pointerClick(element: HTMLElement) {
  element.scrollIntoView({ block: 'center', inline: 'nearest' });
  // A person clicks what is painted: wait for an opening dialog to finish
  // animating in over the page before aiming at its button.
  await waitUntil(
    () => {
      const hit = deepHit(element);
      return !!hit && (hit === element || element.contains(hit));
    },
    'The control must be the topmost element under the pointer',
    { timeout: 2000 }
  );
  const box = element.getBoundingClientRect();
  await sendMouse({
    type: 'click',
    position: [
      Math.round(box.x + box.width / 2),
      Math.round(box.y + box.height / 2),
    ],
  });
}
describe('resource editors above actual page controls', function () {
  this.timeout(10000);
  let api: MockApi;
  let view: LitElement;
  beforeEach(() => signInForTest());
  afterEach(async () => {
    view?.remove();
    api?.restore();
    localStorage.clear();
    await setViewport({ width: 1024, height: 768 });
  });
  for (const kind of [
    'managed_agent',
    'ai_model',
    'flow',
  ] as ResourceAccessViewKind[])
    for (const width of [1440, 390]) {
      it(`${kind} allows normal Access/Tags Cancel clicks at ${width}px`, async () => {
        await setViewport({ width, height: 900 });
        const summary = resourceSummary();
        summary.document.resource_type = kind;
        summary.supported_actions =
          kind === 'managed_agent'
            ? ['resource:view']
            : kind === 'flow'
              ? ['resource:view', 'flow:run']
              : ['resource:view', 'model:invoke'];
        api = mockApi({
          capabilities: ['resource_access', 'abac_rules'],
          routes: [
            { path: /\/access\/resources\//, body: summary },
            { path: /\/tags\//, body: { tags: { env: 'review' } } },
            ...directoryRoutes,
          ],
          fallback: { status: 200, body: [] },
        });
        view = resourceAccessView(kind);
        document.body.append(view);
        await waitUntil(
          () => !!view.shadowRoot!.querySelector('resource-access-panel')
        );
        const panel = view.shadowRoot!.querySelector<ResourceAccessPanel>(
          'resource-access-panel'
        )!;
        await waitUntil(
          () =>
            !!control(panel, 'access-control') &&
            !!control(panel, 'tags-control')
        );
        for (const name of ['access', 'tags']) {
          const trigger = control(panel, `${name}-control`);
          trigger.focus();
          await sendKeys({ press: 'Enter' });
          await waitUntil(() => !!control(panel, 'editor-cancel'));
          if (width > 640) {
            await waitUntil(
              () => !!panel.shadowRoot!.querySelector(':popover-open')
            );
            const title =
              panel.shadowRoot!.querySelector<HTMLElement>('#editor-title')!;
            const box = title.getBoundingClientRect();
            const hit = panel.shadowRoot!.elementFromPoint(
              box.x + box.width / 2,
              box.y + box.height / 2
            );
            expect(
              hit,
              'Page controls must not paint over the editor heading'
            ).to.equal(title);
          }
          await pointerClick(control(panel, 'editor-cancel'));
          await waitUntil(
            () => !control(panel, 'editor-cancel'),
            'Normal Cancel click must reach the editor',
            { timeout: 1200 }
          );
          expect(panel.shadowRoot!.activeElement).to.equal(trigger);
          trigger.focus();
          await sendKeys({ press: 'Enter' });
          await waitUntil(() => !!control(panel, 'editor-cancel'));
          await sendKeys({ press: 'Escape' });
          await waitUntil(() => !control(panel, 'editor-cancel'));
          expect(panel.shadowRoot!.activeElement).to.equal(trigger);
          if (width > 640) {
            trigger.focus();
            await sendKeys({ press: 'Enter' });
            await waitUntil(() => !!control(panel, 'editor-cancel'));
            await sendMouse({ type: 'click', position: [2, 2] });
            await waitUntil(() => !control(panel, 'editor-cancel'));
          }
        }
        expect(document.documentElement.scrollWidth).to.be.at.most(width);
        expect(api.calls.filter((c) => c.method !== 'GET')).to.have.length(0);
      });
    }
  it('uses a desktop dialog fallback when native popovers are unavailable', async () => {
    await setViewport({ width: 1440, height: 900 });
    const descriptor = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'showPopover'
    )!;
    delete (HTMLElement.prototype as { showPopover?: unknown }).showPopover;
    try {
      api = mockApi({
        capabilities: ['resource_access', 'abac_rules'],
        routes: [
          { path: /\/access\/resources\//, body: resourceSummary() },
          { path: /\/tags\//, body: { tags: { env: 'review' } } },
          ...directoryRoutes,
        ],
        fallback: { status: 200, body: [] },
      });
      view = resourceAccessView('managed_agent');
      document.body.append(view);
      await waitUntil(
        () => !!view.shadowRoot!.querySelector('resource-access-panel')
      );
      const panel = view.shadowRoot!.querySelector<ResourceAccessPanel>(
        'resource-access-panel'
      )!;
      await waitUntil(
        () =>
          !!control(panel, 'access-control') && !!control(panel, 'tags-control')
      );
      for (const name of ['access', 'tags']) {
        const trigger = control(panel, `${name}-control`);
        trigger.focus();
        await sendKeys({ press: 'Enter' });
        await waitUntil(
          () => !!panel.shadowRoot!.querySelector('sl-dialog[open]')
        );
        expect(panel.shadowRoot!.querySelector('.desktop-overlay')).to.equal(
          null
        );
        await pointerClick(control(panel, 'editor-cancel'));
        await waitUntil(() => !control(panel, 'editor-cancel'));
        expect(panel.shadowRoot!.activeElement).to.equal(trigger);
      }
    } finally {
      Object.defineProperty(HTMLElement.prototype, 'showPopover', descriptor);
    }
  });
});
