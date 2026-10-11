import { html, nothing, type TemplateResult } from 'lit';

/** One way a captured result.json failed the flow result schema (#868). */
export interface ResultSchemaError {
  path: string;
  message: string;
}

/** The capture-time check of result.json against the flow result schema. */
export interface ResultSchemaValidation {
  valid: boolean;
  errors?: ResultSchemaError[];
  error_count?: number;
}

/**
 * The execution page's list of result schema errors. Renders nothing for a
 * flow without a schema or a document that matched it.
 */
export function renderResultSchemaErrors(
  validation: ResultSchemaValidation | null | undefined
): TemplateResult | typeof nothing {
  if (!validation || validation.valid !== false) return nothing;
  const errors = validation.errors || [];
  const total = Math.max(
    validation.error_count ?? errors.length,
    errors.length
  );
  const hidden = total - errors.length;
  return html`
    <section class="output-section" data-testid="result-schema-errors">
      <h2 class="section-title">Result schema errors</h2>
      <p>
        The result.json this run wrote does not match the result schema set on
        the flow. The result is stored below as written.
      </p>
      <ul>
        ${errors.map(
          (error) =>
            html`<li data-path=${error.path}>
              <code>${error.path}</code>: ${error.message}
            </li>`
        )}
      </ul>
      ${hidden > 0 ? html`<p>${hidden} more not shown.</p>` : nothing}
    </section>
  `;
}
