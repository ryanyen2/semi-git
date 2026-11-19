// The workbench webview's protocol, host side, and its page. `Protocol` answers what the webview
// asks; everything it does to VS Code goes through `Host`, so `protocol.test.ts` can feed it the
// fixtures under `node --test`.
//
// Each reply carries the request's `id`. For previews, details and finds only the newest request
// of its kind is answered: an older one still queued never runs, and one that finished after a
// newer one arrived is dropped. An apply is always answered.

import { StaleRequestError } from "./cliSeam.ts";
import type { Verb } from "./cliSeam.ts";
import type { FindView, Plan, ShowView, Snapshot } from "./types.ts";

export type FromWebview =
  | { type: "ready" }
  | { type: "preview" | "apply"; id: number; verb: Verb; addresses: string[]; whole?: boolean }
  | { type: "show"; id: number; address: string }
  | { type: "find"; id: number; query: string }
  | { type: "name"; addresses: string[] }
  | { type: "rename"; label: string }
  | { type: "resolve"; address: string }
  | { type: "openStep"; address: string }
  | { type: "openFile"; path: string; line?: number }
  | { type: "render"; address: string | null }
  | { type: "copy"; text: string }
  | { type: "init" };

export type ToWebview =
  | ({ type: "state" } & Snapshot)
  | { type: "plan"; id: number; plan: Plan }
  | { type: "plan"; id: number; error: string }
  | { type: "detail"; id: number; show: ShowView }
  | { type: "found"; id: number; hits: FindView["hits"] }
  | { type: "busy"; verb: string | null }
  | { type: "focus"; address: string };

export interface Host {
  snapshot(): Snapshot;
  preview(verb: Verb, addresses: string[], whole: boolean, stillWanted: () => boolean): Promise<Plan>;
  apply(verb: Verb, addresses: string[], whole: boolean): Promise<Plan>;
  show(address: string, stillWanted: () => boolean): Promise<ShowView>;
  find(query: string, stillWanted: () => boolean): Promise<FindView>;
  refresh(): Promise<void>;
  post(msg: ToWebview): void;
  /** The messages that open something in VS Code or ask the user. */
  act(msg: Exclude<FromWebview, { id: number } | { type: "ready" }>): Promise<void> | void;
}

const oneLine = (e: unknown) => String((e as Error)?.message ?? e).split("\n")[0];

export class Protocol {
  private host: Host;
  private latest = { preview: -1, show: -1, find: -1 };

  constructor(host: Host) {
    this.host = host;
  }

  async receive(msg: FromWebview): Promise<void> {
    const host = this.host;
    switch (msg.type) {
      case "ready":
        host.post({ type: "state", ...host.snapshot() });
        return;
      case "preview": {
        const id = (this.latest.preview = msg.id);
        const wanted = () => this.latest.preview === id;
        await this.answer(wanted, () => host.preview(msg.verb, msg.addresses, !!msg.whole, wanted),
          (plan) => ({ type: "plan", id, plan }), (error) => ({ type: "plan", id, error }));
        return;
      }
      case "apply": {
        // An apply also retires any preview still pending: the record it described is gone.
        this.latest.preview = msg.id;
        let reply: ToWebview;
        try {
          reply = { type: "plan", id: msg.id, plan: await host.apply(msg.verb, msg.addresses, !!msg.whole) };
        } catch (e) {
          reply = { type: "plan", id: msg.id, error: oneLine(e) };
        }
        host.post(reply);
        await host.refresh();
        return;
      }
      case "show": {
        const id = (this.latest.show = msg.id);
        const wanted = () => this.latest.show === id;
        await this.answer(wanted, () => host.show(msg.address, wanted), (show) => ({ type: "detail", id, show }),
          (message) => ({ type: "detail", id, show: { ok: false, address: msg.address, message } }));
        return;
      }
      case "find": {
        const id = (this.latest.find = msg.id);
        const wanted = () => this.latest.find === id;
        await this.answer(wanted, () => host.find(msg.query, wanted), (view) => ({ type: "found", id, hits: view.hits }),
          () => ({ type: "found", id, hits: [] }));
        return;
      }
      default:
        await host.act(msg);
    }
  }

  private async answer<T>(
    wanted: () => boolean,
    run: () => Promise<T>,
    ok: (value: T) => ToWebview,
    fail: (error: string) => ToWebview,
  ): Promise<void> {
    let reply: ToWebview;
    try {
      reply = ok(await run());
    } catch (e) {
      if (e instanceof StaleRequestError) return;
      reply = fail(oneLine(e));
    }
    if (wanted()) this.host.post(reply);
  }
}

/** The workbench page: the stylesheet, the script, and nothing else. `workbench.js` builds the DOM. */
export function workbenchHtml(cspSource: string, script: string, style: string, nonce: string): string {
  const csp = [
    "default-src 'none'",
    `style-src ${cspSource}`,
    `script-src 'nonce-${nonce}'`,
    `img-src ${cspSource} data:`,
    `font-src ${cspSource}`,
  ].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${style}">
</head>
<body><script nonce="${nonce}" src="${script}"></script></body>
</html>`;
}
