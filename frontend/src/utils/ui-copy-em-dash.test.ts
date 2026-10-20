/**
 * No em dashes in UI copy (DESIGN.md copy rules). Three survived in rendered
 * strings: the Talk step summary, the push-test note on Notifications and
 * the Users edit dialog (U-10). The test reads the modules as the browser
 * receives them, where comments are already stripped and only rendered
 * strings remain.
 */
import { expect } from '@open-wc/testing';

const MODULES = [
  '/src/components/session-chat-view.ts',
  '/src/views/authed/notification-preferences-view.ts',
  '/src/views/authed/settings/user-management-view.ts',
];

describe('UI copy has no em dashes (U-10)', () => {
  for (const path of MODULES) {
    it(`keeps em dashes out of ${path}`, async () => {
      const response = await fetch(path);
      expect(response.ok, path).to.be.true;
      const code = (await response.text())
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/<!--[\s\S]*?-->/g, '')
        .split('\n')
        .filter((line) => !line.trim().startsWith('//'))
        .join('\n');
      expect(code).to.not.contain('—');
      expect(code).to.not.contain('&mdash;');
    });
  }
});

describe('one glyph for a missing value (U-11)', () => {
  for (const path of [
    '/src/views/authed/flow-executions-view.ts',
    '/src/views/authed/flow-execution-view.ts',
  ]) {
    it(`uses "-", never an em dash, in ${path}`, async () => {
      const code = (await (await fetch(path)).text())
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .split('\n')
        .filter((line) => !line.trim().startsWith('//'))
        .join('\n');
      expect(code).to.not.contain('—');
      expect(code).to.not.contain('\\u2014');
    });
  }
});
