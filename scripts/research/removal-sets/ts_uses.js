// Ask the TypeScript compiler, for every name used in the project, where it is declared.
// Prints [[file, line, declaring file, declaring line], ...] for declarations inside src/.
const path = require("path");
const root = process.argv[2];
const ts = require(path.join(root, "node_modules/typescript"));
const cfg = ts.readConfigFile(path.join(root, "tsconfig.json"), ts.sys.readFile);
const parsed = ts.parseJsonConfigFileContent(cfg.config, ts.sys, root);
const program = ts.createProgram(parsed.fileNames, parsed.options);
const checker = program.getTypeChecker();
const srcDir = path.join(root, "src") + path.sep;
const rel = (f) => "editor/vscode/" + path.relative(root, f);
const out = new Set();
for (const sf of program.getSourceFiles()) {
  if (!sf.fileName.startsWith(srcDir)) continue;
  const visit = (node) => {
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) {
      const sym = checker.getSymbolAtLocation(node);
      const syms = [];
      if (sym) {
        syms.push(sym);
        if (sym.flags & ts.SymbolFlags.Alias) { try { syms.push(checker.getAliasedSymbol(sym)); } catch (e) {} }
      }
      const uline = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
      for (const s of syms) for (const d of s.declarations || []) {
        const dsf = d.getSourceFile();
        if (!dsf.fileName.startsWith(srcDir)) continue;
        const dline = dsf.getLineAndCharacterOfPosition(d.getStart()).line + 1;
        if (dsf === sf && dline === uline) continue;
        const col = sf.getLineAndCharacterOfPosition(node.getStart()).character;
        const dname = d.name && d.name.text ? d.name.text : "";
        out.add(JSON.stringify([rel(sf.fileName), uline, rel(dsf.fileName), dline, col, node.text, dname]));
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
}
console.log("[" + [...out].join(",") + "]");
