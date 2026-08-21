// Which `map_view` nodes a listing surface should show.
//
// A feature whose ops touch only the residue/anchor sentinels has nothing anyone can act on: `sgt
// show` answers it with "0 symbols in 0 files" and reverting it removes nothing. The terminal has
// dropped those rows for a while (`sgt/tui/graph.py`'s lane filter and `_print_map_tree`'s
// `is_visible`) while the sidebar tree and the feature quick-pick showed every node the projection
// carried, so the three surfaces listed different features for one and the same repo -- and the
// pick offered husks as revert targets. Pure, and in its own module rather than inline in the tree
// provider, because both surfaces need the same answer and neither can be loaded under `node --test`
// once it imports `vscode`.

// A type-only import: node strips it, so this module stays loadable under `node --test` (a value
// import would survive into a runtime lookup for an extensionless path and fail to resolve).
import type { MapNode } from "./types";

/**
 * True when this leaf's own work touches at least one real symbol.
 *
 * A node with no `own_symbols` field at all is *unknown*, not empty -- an older projection did not
 * carry the field -- so it counts as present, the same way the Python filter's `("?",)` default does.
 */
export function hasOwnSymbols(node: MapNode): boolean {
  return node.own_symbols == null || node.own_symbols.length > 0;
}

/**
 * True when this node is worth a row: a feature with symbols of its own, or a subsystem with at
 * least one visible descendant. A subsystem left holding nothing but husks is dropped with them.
 */
export function isVisibleNode(node: MapNode, nodeOf: (id: string) => MapNode | undefined): boolean {
  if (!node.children.length) {
    return hasOwnSymbols(node);
  }
  return node.children.some((id) => {
    const child = nodeOf(id);
    return !!child && isVisibleNode(child, nodeOf);
  });
}
