import { expect, fixture, fixtureCleanup, html } from '@open-wc/testing';
import { render } from 'lit';
import sinon, { SinonSandbox } from 'sinon';
import './preloop-flow-form.ts';
import type { PreloopFlowForm } from './preloop-flow-form';
import { renderResultSchemaErrors } from '../utils/result-schema';

/**
 * #868: a flow may set a JSON Schema for its result.json. The form edits it
 * as text with a "Validate result" toggle, and the execution page lists the
 * errors of a run whose result did not match.
 */
describe('PreloopFlowForm result schema', () => {
  let sandbox: SinonSandbox;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-access-token');
    localStorage.setItem('refreshToken', 'test-refresh-token');
    sandbox = sinon.createSandbox();
    sandbox
      .stub(window, 'fetch')
      .callsFake(async () => new Response(JSON.stringify([])));
  });

  afterEach(() => {
    fixtureCleanup();
    sandbox.restore();
    localStorage.clear();
    sessionStorage.clear();
  });

  const mount = async (
    extra: Record<string, unknown> = {}
  ): Promise<PreloopFlowForm> => {
    const flow = {
      name: 'Analysis',
      prompt_template: 'Answer one question',
      agent_type: 'codex',
      ...extra,
    };
    const element = await fixture<PreloopFlowForm>(
      html`<preloop-flow-form .flow=${flow}></preloop-flow-form>`
    );
    while ((element as any)._loadingReferenceData) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    await element.updateComplete;
    return element;
  };

  const field = (element: PreloopFlowForm, name: string): any => {
    const node = element.shadowRoot!.querySelector<any>(
      `[data-field="${name}"]`
    );
    expect(node, name).to.exist;
    return node;
  };

  const type = async (element: PreloopFlowForm, text: string) => {
    const textarea = field(element, 'result_schema');
    textarea.value = text;
    textarea.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await element.updateComplete;
  };

  const submit = async (
    element: PreloopFlowForm
  ): Promise<Record<string, unknown> | null> => {
    const listener = sandbox.spy();
    element.addEventListener('flow-submit', listener);
    await (element as any).handleFormSubmit(new Event('submit'));
    return listener.callCount ? listener.firstCall.args[0].detail.flow : null;
  };

  it('does not resend an untouched schema', async () => {
    const schema = { type: 'object', required: ['verdict'] };
    const element = await mount({ result_schema: schema });
    expect(JSON.parse(field(element, 'result_schema').value)).to.deep.equal(
      schema
    );
    const payload = await submit(element);
    expect(payload).to.not.have.property('result_schema');
  });

  it('submits a typed schema and the toggle', async () => {
    const element = await mount();
    expect(field(element, 'validate_result').disabled).to.equal(true);
    await type(element, '{"type": "object", "required": ["verdict"]}');
    const toggle = field(element, 'validate_result');
    expect(toggle.disabled).to.equal(false);
    expect(toggle.checked).to.equal(true);
    toggle.checked = false;
    toggle.dispatchEvent(new CustomEvent('sl-change', { bubbles: true }));
    await element.updateComplete;
    const payload = await submit(element);
    expect(payload?.result_schema).to.deep.equal({
      type: 'object',
      required: ['verdict'],
    });
    expect(payload?.validate_result).to.equal(false);
  });

  it('clears the schema with null when the text is emptied', async () => {
    const element = await mount({ result_schema: { type: 'object' } });
    await type(element, '  ');
    const payload = await submit(element);
    expect(payload?.result_schema).to.equal(null);
  });

  it('refuses text that is not a JSON object with a readable error', async () => {
    const element = await mount();
    await type(element, '{"type": ');
    expect(await submit(element)).to.equal(null);
    expect((element as any).formError).to.match(
      /^Result schema is not valid JSON/
    );
    await type(element, '[1, 2]');
    expect(await submit(element)).to.equal(null);
    expect((element as any).formError).to.equal(
      'Result schema must be a JSON object (a JSON Schema).'
    );
  });
});

describe('renderResultSchemaErrors', () => {
  const mountErrors = (validation: any): HTMLElement => {
    const host = document.createElement('div');
    render(renderResultSchemaErrors(validation), host);
    return host;
  };

  it('renders nothing without a schema or for a valid result', () => {
    expect(mountErrors(null).querySelector('section')).to.equal(null);
    expect(
      mountErrors({ valid: true, errors: [] }).querySelector('section')
    ).to.equal(null);
  });

  it('lists each error with its path and says how many are hidden', () => {
    const host = mountErrors({
      valid: false,
      errors: [
        { path: '$.verdict', message: "'verdict' is a required property" },
      ],
      error_count: 3,
    });
    const items = host.querySelectorAll('li');
    expect(items.length).to.equal(1);
    expect(items[0].getAttribute('data-path')).to.equal('$.verdict');
    expect(items[0].textContent).to.contain("'verdict' is a required property");
    expect(host.textContent).to.contain('2 more not shown.');
  });
});
