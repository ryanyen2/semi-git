// Mirror of the JSON shapes from `sgt.api` (the canonical projection). Keep in sync with
// sgt/api.py — the CLI `--json` mode, MCP, and this extension all consume the same schema.

export interface NodeView {
  id: string;
  kind: string;
  status: string;
  intent: string;
  depends_on: string[];
  dependents: string[];
  provenance: string[];
  commits: string[];
  conflict: string | null;
  effects?: EffectView[];
  witness?: { reason: string; held: string[]; against: string[] };
}

export interface EffectView {
  op: string;
  target: string;
  file: string;
}

export interface Edge {
  src: string;
  dst: string;
  type: string;
}

export interface GraphView {
  nodes: NodeView[];
  edges: Edge[];
  count: number;
}

export interface StatusView {
  nodes: number;
  files?: { path: string; lines: number }[];
  effects?: number;
  drift?: { any: boolean; modified: string[]; added: string[]; deleted: string[]; summary: string };
  error?: string;
}

export interface BlameSpan {
  start: number; // 1-based inclusive
  end: number; // 1-based inclusive
  node_id: string | null;
}

export interface BlameView {
  file: string;
  spans: BlameSpan[];
  nodes: Record<string, { intent: string; kind: string; status: string }>;
  drift: boolean;
  error?: string;
}

export interface EmitView {
  ok: boolean;
  action?: string;
  node_id?: string;
  message?: string;
  removed?: string[];
  files?: Record<string, { before: string; after: string }>;
  error?: string;
}
