/** Globals injected into the extension's webviews by VS Code and script tags. */

/** API object VS Code injects into every webview before scripts run. */
interface VsCodeWebviewApi {
	postMessage(message: unknown): void;
	getState(): unknown;
	setState(state: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeWebviewApi;

/** Exposed on `globalThis` by `media/shared/qr.js` when loaded via a script tag. */
declare const QrSvg: {
	render(text: string, border?: number): string;
};
