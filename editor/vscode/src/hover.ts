// Hover on a definition's first line (or a whole file's): its name, its steps newest first with
// the save's subject, how long ago, and whether the step is out, then links to preview taking it
// out and to name it. Only that line, so reading the body does not raise it on every token.

import * as vscode from "vscode";
import { Documents } from "./documents";
import { ago, headLine, nameOf } from "./units";

const MAX_STEPS = 10;

const md = (s: string) => s.replace(/([\\`*_{}[\]()#+\-.!|<>])/g, "\\$1");
const link = (label: string, command: string, address: string) =>
  `[${label}](command:${command}?${encodeURIComponent(JSON.stringify([address]))})`;

export class HoverProvider implements vscode.HoverProvider {
  constructor(private readonly docs: Documents) {}

  async provideHover(doc: vscode.TextDocument, pos: vscode.Position): Promise<vscode.Hover | undefined> {
    const hit = await this.docs.at(doc, pos.line);
    if (!hit || hit.unit.kind === "head") return undefined;
    const { unit, show } = hit;
    const line = headLine(unit, doc.getText().split("\n"));
    if (pos.line !== line - 1) return undefined;
    const name = nameOf(unit.steps);
    const out = new vscode.MarkdownString(undefined, true);
    out.isTrusted = { enabledCommands: ["ugh.takeOut", "ugh.name"] };
    if (name) out.appendMarkdown(`**${md(name)}**\n\n`);
    const newest = [...unit.steps].reverse();
    for (const st of newest.slice(0, MAX_STEPS)) {
      const sha = st.address.slice(st.address.lastIndexOf("@") + 1);
      const save = show.saves.find((s) => s.sha.startsWith(sha));
      const parts = [`\`${sha}\` ${md(save?.subject ?? "")}`];
      if (st.name && st.name !== name) parts.push(md(st.name));
      if (save?.date) parts.push(ago(save.date));
      if (st.out) parts.push("out");
      out.appendMarkdown(parts.join(" · ") + "  \n");
    }
    if (newest.length > MAX_STEPS) out.appendMarkdown(`${newest.length - MAX_STEPS} more  \n`);
    out.appendMarkdown(`\n${link("Take out", "ugh.takeOut", unit.unit)} · ${link("Name", "ugh.name", unit.unit)}`);
    return new vscode.Hover(out, doc.lineAt(line - 1).range);
  }
}
