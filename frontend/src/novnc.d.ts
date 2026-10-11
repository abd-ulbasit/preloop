/**
 * Minimal typings for the vendored noVNC (`vendor/novnc`, MPL-2.0). Only the
 * surface `components/desktop-view.ts` uses is declared.
 */
declare module '@novnc/novnc' {
  export interface RFBOptions {
    shared?: boolean;
    credentials?: Record<string, string>;
    repeaterID?: string;
    wsProtocols?: string[];
  }

  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement,
      urlOrChannel: string | WebSocket,
      options?: RFBOptions
    );
    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    clipViewport: boolean;
    focusOnClick: boolean;
    background: string;
    disconnect(): void;
  }
}
