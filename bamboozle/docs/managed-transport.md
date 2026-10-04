# Managed Tessera delivery

Managed transport adds local recipient custody, key rotation/revocation and replay
admission to Bamboozle's existing file and project exchanges. Select `--peer NAME`
on CLI, or `peer` on operator MCP. On send, NAME is the destination; on receipt,
NAME is the expected sender. The local identity comes from `identity-init`.
Semantic translation, classical recipes and BAMb5 remain separate operations.

## Establish a pair of local workspaces

These PowerShell commands demonstrate explicit same-host provisioning. For remote
operators, transfer the provisioning key through an independently trusted channel,
verify the peer name and fingerprint, and protect both operators' filesystems.
Never include provisioning keys in the project packet or neutral agent directory.
Each new pair requires a fresh random key; never share a pair key among peers.

```powershell
$owner = 'C:\work\owner'
$receiver = 'C:\work\receiver'
New-Item -ItemType Directory -Path $owner, $receiver
bamboozle --root $owner identity-init owner
bamboozle --root $receiver identity-init receiver
bamboozle --root $owner keygen --key provision.key
Copy-Item -LiteralPath "$owner\provision.key" -Destination "$receiver\provision.key"
bamboozle --root $owner recipient-register receiver --key provision.key
bamboozle --root $receiver recipient-register owner --key provision.key
bamboozle --root $owner identity-status
bamboozle --root $receiver identity-status
```

Registration copies the 32-byte key into private `.vigiles/identity/keys/` and
records its SHA-256 fingerprint. Reports contain fingerprints and lifecycle events,
never secret bytes. Provisioning files remain under operator custody; the product
does not delete keys or silently rotate them. Initialization never replaces an
existing identity. Names use 1–128 ASCII letters, digits, dots, underscores or
hyphens. A registry accepts at most 1 MiB of retained metadata and history.

For a file already present at the owner, encryption and authenticated receipt are:

```powershell
bamboozle --root $owner seal payload.bin -o payload.bam --peer receiver --ttl 3600
Copy-Item -LiteralPath "$owner\payload.bam" -Destination "$receiver\payload.bam"
$opened = bamboozle --root $receiver open payload.bam -o delivered.bin --peer owner | ConvertFrom-Json
bamboozle --root $receiver delivery-status $opened.delivery_id
```

Outputs must be new paths. If receipt or publication is interrupted, inspect the
reported delivery ID, retain all partial files, correct the reported conflict,
then repeat the exact `open` command with `--recover`. Recovery cannot choose a
different output, replace conflicting bytes or accept a different packet.

## Complete project development

Use the normal scoped project policy and dictionary described in the README. The
following continues an exported `$viewId` at `$owner`; all secret state stays
outside the receiver's `work` directory.

```powershell
bamboozle --root $owner view-pack $viewId -o outbound.bam --peer receiver
Copy-Item -LiteralPath "$owner\outbound.bam" -Destination "$receiver\outbound.bam"
$opened = bamboozle --root $receiver exchange-open outbound.bam -o work --peer owner | ConvertFrom-Json
# Develop and execute the entire application in $receiver\work.
bamboozle --root $receiver exchange-return $opened.exchange_id -o return.bam --peer owner
Copy-Item -LiteralPath "$receiver\return.bam" -Destination "$owner\return.bam"
$review = bamboozle --root $owner view-review $viewId --return-packet return.bam --peer receiver | ConvertFrom-Json
$review.changes | Format-List
# After examining decoded changes, all diagnostic pages and conflicts:
bamboozle --root $owner view-apply $viewId --return-packet return.bam --peer receiver --review-sha256 $review.review_sha256
bamboozle --root $owner delivery-status $review.delivery_id
# Execute the restored original-vocabulary application and its checks.
```

Optional `--recipe` still selects an explicitly provisioned private classical
recipe at both ends. Outbound and return packing accept `--ttl`; incoming operations
use the authenticated lifetime. Managed received projects must return to their
original registered sender using managed transport. A key rotation can occur
between outbound and return. The frame and saved translation context stay unchanged.

Extraction recovery repeats its packet, output, peer and recipe with `--recover`.
Source import recovery uses `view-apply ID --review-sha256 SHA --recover`, omitting
packet, peer, key, recipient, recipe and deletion arguments. It checks current key
authorization against the retained authenticated certificate before further writes.
It needs the registered key file, but not the original provisioning file or packet.
Historical `view-status`, `exchange-context` and `delivery-status` remain readable
after revocation. Even a managed return with no edits is consumed on successful apply.

## Rotate and revoke deliberately

Generate and separately provision a new pair key. At **each** workspace inspect
`identity-status`, then rotate the corresponding peer with that exact registry hash:

```powershell
$registry = bamboozle --root $owner identity-status | ConvertFrom-Json
$oldKey = $registry.peers.receiver.active
bamboozle --root $owner recipient-rotate receiver --key replacement.key --expected-sha256 $registry.registry_sha256
# Perform the matching rotation for peer 'owner' at the receiver.
$registry = bamboozle --root $owner identity-status | ConvertFrom-Json
bamboozle --root $owner recipient-revoke receiver --key-id $oldKey --expected-sha256 $registry.registry_sha256
```

| Key state | Send | Receive and pending recovery |
| --- | --- | --- |
| Active | Allowed | Allowed, subject to route, lifetime and replay checks |
| Retired by rotation | Refused | Only messages issued no later than retirement; new admissions must still be unexpired |
| Revoked | Refused | Refused, including pending operations |

Revocation is permanent for that fingerprint. Revoking the active key leaves no
active sender until an explicit rotation provisions a fresh key. Peer bindings
retain old fingerprints; a key cannot be reused for another peer or reactivated.
Events use whole UTC seconds, so the retirement boundary includes its recorded
second. Clock synchronization and bilateral rotation remain operator responsibilities.
Revocation cannot erase plaintext already published or invalidate its historical receipt.

## Wire and admission contract

Schema 2 authenticates `kind`, `schema_version`, `algorithm`, `sender`, `recipient`,
`key_id`, `message_id`, `issued_at`, `expires_at` and `purpose` as canonical JSON
associated data. Purpose is `file`, `project-outbound` or `project-return`.
Message IDs are random UUID4 hex strings; each encryption uses a fresh 12-byte nonce.
Payload/packet bounds remain 16/32 MiB. Lifetime is 1–604800 seconds (default 86400);
new admission refuses expiry and issuance more than 60 seconds ahead of local time.
The underlying primitive and authenticated-data behavior follow the
[cryptography AESGCM API](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).

Preview authenticates without consuming. Before publication, an immutable private
admission binds the sender/recipient/message ID to the exact packet, payload and
operation completion receipt. Reformatting packet JSON does not create a new
message. An OS process lock excludes competing admissions and cooperative lifecycle
changes throughout publication. Source journals retain authenticated snapshots;
file/extraction journals require the original packet for recovery. Completion is
the existing domain receipt, avoiding a second commit that could disagree after a
crash. Pending operations require explicit matching recovery. An admitted operation
may resume after expiry, but current revocation still blocks it. Records are never
automatically expired or pruned. A journal written before admission can resume only
while fresh; no payload has been published at that point.

Schema 1 remains available through explicit `--key` and `--recipient` together.
It does not gain recipient lifecycle or replay protection. Mixed selectors and
cross-version decoders are refused; managed operations never fall back to raw-key
mode. Operator MCP exposes `initialize_identity`, `recipient_registry`,
`change_recipient`, `inspect_delivery`, `seal_file`, `open_file`, and the existing
project methods with optional `peer`. None enter the source-agent MCP tool pool.

The trust boundary is a locally protected operator workspace, key files, journals,
receipts and wall clock. Shared-key possession does not independently establish
human identity or nonrepudiation. This implementation does not distribute keys,
coordinate replay across cloned hosts, prevent administrative rollback of state,
set filesystem ACLs or supply OS sandboxing. It supplies explicit local key-use
periods and terminal revocation; it does not claim certification against
[NIST SP 800-57 Part 1](https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final).
