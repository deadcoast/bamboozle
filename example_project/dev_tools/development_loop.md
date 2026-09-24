# Bamboozle development loop

From the repository root, run `.\AI\dev_tools\bamboozle.ps1` for the guided terminal workflow. Paste the file path, review the diff, then choose Save. Enter cancels the save prompt. The commands below use the installed `bamboozle` command; the PowerShell launcher accepts the same arguments.

1. Preview and encode:

   ```powershell
   bamboozle preview node.py
   bamboozle encode node.py
   bamboozle verify node_safe.py
   ```

   Encoding creates `node_safe.py` and `.node_safe.py.scm_ledger.json`. Keep the ledger local. Preserve all `__SCM_LIT_...__` markers when editing the translated file.

2. Review or edit `node_safe.py` using your usual tools. Restoration automatically uses the dictionary saved in its ledger, including custom mappings. An explicit `--dict` must match that saved dictionary. Newly introduced unmarked target terms will also be reverse-mapped.

3. Preview restoration and decode:

   ```powershell
   bamboozle preview node_safe.py --reverse
   bamboozle decode node_safe.py -o node_ready.py
   ```

   Add `--context 0` to Preview to show only changed lines. In the terminal menu, **Refresh** reloads the source and shows a fresh preview before saving. Add `--strict` to Decode when you require the unchanged encoded file. Add `--keep-ledger` when restoring to a separate output if you want repeated verification/restoration. Existing outputs are protected unless you explicitly use `--force` or confirm OVERWRITE in the interactive workflow. A separate destination with its own cipher ledger requires another output path.

4. Review and test the restored file before using it. See the [complete usage guide](README.md) for dictionary rules, integrity reports, and compatibility.

If a forward operation saved the encoded file but its ledger is missing, retain both files. Run `bamboozle recover node_safe.py --original node.py` to check the exact pair without writes; add the same `--dict` if a custom mapping was used. Repeat with `--apply` to create only the missing ledger, then run Verify. Recovery refuses edited pairs and existing ledgers and needs a separate original file.
