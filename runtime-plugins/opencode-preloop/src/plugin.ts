// OpenCode treats every root export as a plugin factory. Keep implementation
// helpers and singleton state in index.js, outside this loader entrypoint.
export { PreloopPlugin, PreloopPlugin as default } from "./index.js";
