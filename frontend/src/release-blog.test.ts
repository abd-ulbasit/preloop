import { expect } from '@open-wc/testing';
import { releaseBlogFallback, releaseBlogUrl } from './release-blog';

describe('release blog navigation', () => {
  it('expands release templates and preserves custom brand links', () => {
    const template = 'https://preloop.ai/blog/preloop-{version}';
    for (const version of ['0.17.1', 'v0.17.1-12-gabc']) {
      expect(releaseBlogUrl(template, version)).to.equal(
        'https://preloop.ai/blog/preloop-0-17-1'
      );
    }
    expect(releaseBlogUrl(template, '')).to.equal('https://preloop.ai/blog');
    expect(releaseBlogUrl(template, 'unknown')).to.equal(
      'https://preloop.ai/blog'
    );
    expect(releaseBlogUrl('https://example.com/news', '1.2.3')).to.equal(
      'https://example.com/news'
    );
    expect(releaseBlogUrl(undefined, '1.2.3')).to.equal(undefined);
  });
  it('preserves published releases and unrelated missing articles', () => {
    expect(releaseBlogFallback('preloop-0-17-0', ['preloop-0-17-0'])).to.equal(
      null
    );
    expect(
      releaseBlogFallback('a-missing-article', ['preloop-0-17-0'])
    ).to.equal(null);
  });
  it('finds the nearest earlier numeric release in one hop', () => {
    const posts = [
      'preloop-0-9-0',
      'preloop-1-0-0',
      'preloop-0-16-0',
      'preloop-0-17-0',
    ];
    expect(releaseBlogFallback('preloop-0-17-1', posts)).to.equal(
      '/blog/preloop-0-17-0'
    );
    expect(
      releaseBlogFallback(
        'preloop-0-17-1',
        posts.filter((slug) => slug !== 'preloop-0-17-0')
      )
    ).to.equal('/blog/preloop-0-16-0');
    expect(releaseBlogFallback('preloop-0-10-0', posts)).to.equal(
      '/blog/preloop-0-9-0'
    );
    expect(releaseBlogFallback('preloop-0-1-0', posts)).to.equal('/blog');
    expect(releaseBlogFallback('preloop-0-17-1', [])).to.equal('/blog');
  });
});
