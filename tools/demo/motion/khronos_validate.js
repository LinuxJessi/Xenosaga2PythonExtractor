const fs = require('fs');
const validator = require(process.argv[2] + '/node_modules/gltf-validator');
(async () => {
  let bad = 0;
  for (const f of process.argv.slice(3)) {
    const r = await validator.validateBytes(new Uint8Array(fs.readFileSync(f)), {maxIssues: 50});
    const s = r.issues;
    console.log(`${f.split('/').pop()}: validator ${r.validatorVersion}, errors ${s.numErrors}, warnings ${s.numWarnings}, infos ${s.numInfos}, hints ${s.numHints}`);
    const seen = {};
    for (const m of s.messages) { const k = m.code; if (!seen[k]) { seen[k] = 1; console.log(`  [${['error','warning','info','hint'][m.severity]}] ${m.code}: ${m.message} (${m.pointer || ''})`); } }
    bad += s.numErrors;
  }
  process.exit(bad ? 1 : 0);
})();
