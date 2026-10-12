# Two-Stage Deployment v2

An opt-in, existing-VNet private profile derived from the existing
[KT template](../kt/README.md) and [Enterprise template](../enterprise/main.bicep).
It does not replace their Portal buttons or adopt an existing KT installation.
Use a new set of resource names in one explicitly selected resource group.

## Ownership

| User stage | Owns | Does not do |
| --- | --- | --- |
| Infrastructure, once | Foundry account, model deployments, project, BYO connections/host, Storage, Cosmos DB, Search, control-plane UAMI, internal Container Apps Environment, private DNS/PEs, Log Analytics, Entra-only Insights, resource diagnostics, ACS/email/domain, scoped roles, RG tag handoff | No App/Job container, placeholder image, environment variables or Prompt/Hosted Agent versions |
| Application, initially and repeatedly | Agent code/policies through the existing guarded publication workflow; real App/Job image, environment, secrets, registry configuration and Entra web settings | No infrastructure, model, BYO, diagnostic or RBAC replay during image/configuration updates |

The infrastructure command internally creates the foundation, waits for the account
Capability Host, and then completes models/project/BYO/RBAC. These are implementation
steps inside one infrastructure operation, not another application stage. Do not
precreate the project or replay the account's custom subdomain.

The Hosted Agent identity is created by the platform only when its code is first
published. Its project Foundry User, component Monitoring Metrics Publisher and
subscription Reader grants are therefore a narrow **late infrastructure completion**.
[runtime-roles.bicep](runtime-roles.bicep) changes only those grants. It does not replay
the infrastructure and is not part of ordinary App/Job updates. Use the verified
Hosted principal, not the control-plane or project identity. Billing hierarchy access
and separately approved evidence scopes remain operator-managed.

## Files

| Source | Compiled artifact / purpose |
| --- | --- |
| [infrastructure.bicep](infrastructure.bicep) | [infrastructure.json](infrastructure.json), one-time infrastructure |
| [foundation.bicep](foundation.bicep) | Internal foundation; reuses the pinned KT AVM modules and email module |
| [resource-group-tags.bicep](resource-group-tags.bicep) | Completion-only, non-secret RG handoff |
| [application.bicep](application.bicep) | [application.json](application.json), repeatable App/Job installation and updates |
| [runtime-roles.bicep](runtime-roles.bicep) | [runtime-roles.json](runtime-roles.json), late Hosted permissions |
| [monitoring-role.bicep](monitoring-role.bicep), [subscription-reader.bicep](subscription-reader.bicep) | Scoped role modules, never application resources |
| [deploy_v2.py](../../scripts/deploy_v2.py) | Explicit-target preflight and Incremental deployment CLI |
| [test_deployment_v2.py](../../tests/test_deployment_v2.py) | Offline ARM and install/update contract tests |

Never hand-edit the compiled JSON. CI pins Bicep 0.46.1 and checks all three artifacts.

## Prerequisites

- An existing target RG and matching default Azure CLI tenant/subscription. The tool
  does not sign in, switch accounts, create a RG, or read developer `.env` state.
- An approved same-region existing VNet with three distinct prepared IPv4 subnets.
  Preserve the KT CIDR, RFC1918, delegation, exclusivity and free-address requirements.
  The tool does not PUT the VNet or create/change subnets.
- Deployment and role-definition/assignment permissions, plus private DNS reads and
  any explicit canonical central-DNS IDs. Failed or incomplete reads block writes.
- An approved read-only Azure MCP server and project connection for Agent publication.
  v2 records their names/URL, not their credentials or an Entra permission bypass.
  The separate [MCP deployment unit](../azure-mcp-server/README.md) and its Entra
  permissions remain explicit setup work; do not create a second Environment/workspace.
- Actual model catalog, Responses/Agents capabilities and quota. The completion
  preflight reuses the KT checks; aliases alone are not availability proof.
- An approved immutable control-plane image digest and registry reachability.
  Public GHCR needs no token; a private registry needs explicit secret references.
  No image is built, mirrored, published or automatically sent during infrastructure setup.
- A clean customer clone/private-network publication host for the existing Agent
  scripts. App/Job deployment does not bypass their import, pytest, roster or smoke gates.

Model defaults retain the KT physical Terra/Luna version `2026-07-09`, GlobalStandard
and 200 capacity units each. GlobalStandard processing can leave Korea Central.
ACS/email data location defaults to Asia Pacific. Validate policy, price and capacity
before opting into `--mode deploy`.

## Resource Group Handoff

The completed infrastructure merges **29** `azbrief.v2.*` tags into the existing RG
tags. It never removes unrelated tags. `schema=2`, `profile=kt-private-v2` and
`state=ready` are written only after infrastructure, BYO, roles and telemetry bindings
finish. Foundation alone does not produce a ready contract.

Names include `environment`, `app`, `job`, `identity`, `storage`, `cosmos`, `search`,
`workspace`, `insights`, `acs`, `email`, `foundry`, `project`, `hosted`, `coreModel`,
`simpleModel`, `vnetRg`, `vnet`, `peSubnet`, `foundrySubnet` and `appSubnet`.
`deployment` identifies the successful completion output used by the guarded Agent
handoff. Optional MCP URL/connection and the ACA PE flag are also recorded.

Application defaults to `resourceGroup().tags` and resolves IDs/endpoints from names
and actual existing resources in **that same RG**. Operators do not re-enter all
infrastructure names or pass a copied infrastructure-output object on every update.
Tags are editable, plaintext routing metadata, not identity, permission or cryptographic
proof. The CLI also checks explicit context, ownership, typed internal/PNA/subnet state
and exact BYO bindings. Unknown/corrupt/missing contracts block writes.

Azure permits 50 tags per RG and 256 characters per value. v2 reserves 29 slots;
the CLI checks the merged budget before provisioning. Resource tags have a separate
15-tag limit because the same input also reaches Private DNS. Use canonical tag-key
casing. Never store API keys, registry tokens, client secrets or subscriber data here.
See [Azure tag limits](https://learn.microsoft.com/azure/azure-resource-manager/management/tag-resources)
and [Bicep tag application](https://learn.microsoft.com/azure/azure-resource-manager/management/tag-resources-bicep).

## Infrastructure Once

Prepare a protected parameter file based on
[infrastructure.parameters.example.json](infrastructure.parameters.example.json).
Replace every placeholder. Internal stage/reuse flags are derived by the CLI and
must not be supplied. Keep custom parameter/configuration files out of Git and logs.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --parameters <protected-parameters.json> --mode preflight
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --parameters <protected-parameters.json> --mode what-if
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --parameters <protected-parameters.json> --mode deploy
```

`preflight` is the default and is read-only. An initial `what-if` covers the foundation
because the account/project do not exist yet. `deploy` runs fresh preflight, ARM validate
and what-if immediately before **each** internal create operation. All deployments
are Incremental; no Complete mode, resource deletion or automatic migration is used.

After success, infrastructure reruns are blocked by the RG contract. If the foundation
succeeded but completion was interrupted, use explicit `--stage complete` after
diagnosis. It verifies every foundation target and reuses matching ready models,
project/BYO/telemetry rather than replaying their PUTs. An automatic/missing/mismatched
project host needs separately approved recovery, never silent delete/reset.

The ACA Environment remains internal with PNA Disabled. ACA PE automation retains the
KT default `false`: the other four PEs and DNS preparation remain. No endpoint is
deleted by Incremental omission. Manual ACA DNS/HTTPS and all real PE states still
require acceptance. This is not a migration path for an old KT installation.

## Application Install And Updates

Agent publication is part of the application stage, not ARM infrastructure:
use [setup_kt_agents.ps1](../../scripts/setup_kt_agents.ps1) with the completed
deployment name from `azbrief.v2.deployment`, the exact target and an isolated named
azd environment. Its compatible `ktFoundation.runtimeSetup` output is retained.
Complete Configure/Publish/Check using the existing private-network guide. These
steps publish the six distinct Prompt Agents and the Hosted code, not merely names.
Do not overwrite the developer `.env` or skip a failed publication/smoke gate.

After the platform exposes the intended Hosted principal, complete only its grants:

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --hosted-principal-id <verified-hosted-principal-guid> --mode deploy
```

If initial publication exposes an active version but fails a smoke due to those missing
permissions, stop and confirm the exact version/principal, finish the grants, then
rerun the existing Check/smoke. Never treat the failed gate as success or reset the Agent.
Role propagation, Billing access and live tenant evidence are separate acceptance checks.

Install the control plane, then use the same command for every subsequent update:

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 application --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <same-target-rg> --image '<approved-registry/repository@sha256:digest>' --mode preflight
python -m scripts.deploy_v2 application --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <same-target-rg> --image '<approved-registry/repository@sha256:digest>' --configuration <protected-application-configuration.json> --mode deploy
```

Use [application.configuration.example.json](application.configuration.example.json)
as a non-secret patch example. To update only the image, omit `--configuration`.
The file can patch `appEnvironment`, `jobEnvironment`, App/Job configuration/templates,
`webAuthConfiguration` and secure `existingSecrets.app`/`existingSecrets.job` arrays.
Secret entries use `name` plus `value`, or existing Key Vault `keyVaultUrl`/`identity`.
Credential environment variables use `secretRef`; never inline credentials in ordinary
environment parameters. Use an existing same-tenant Entra client ID, customer issuer,
allowed audiences, client-secret reference and explicit admin/reader allow-lists.
v2 does not create directory registrations or allow anonymous Admin/Archive.

Initial defaults: real image on `8000`/`/health`, generated API key, same UAMI/image/key
for App and Job, Admin/Archive/Feedback off, auth required, concurrency 1 and a Manual
Job (`replicaTimeout=43200`, retry limit 0, run budget 39600). Recipients are never
invented. Enabling authenticated UI raises minimum replicas to at least 1 (idle cost).

Updates preserve current secrets, registry configuration, auth, environments, replicas,
resources, unrelated resource tags and accepted scheduler trigger/cron. They never
rotate a key after a read failure, disable authentication, turn a Schedule Job into
Manual or activate a new schedule. A partial App/Job install requires explicit recovery.
Infrastructure-bound identity/endpoint/storage routes cannot be redirected in a patch.
Temporary parameter files are removed and their values are never logged.

Serialize deployments to one RG/App. This tool has no distributed deployment lock,
image provenance/vulnerability certification, automatic live rollback or smoke acceptance.
Review/approve artifacts and what-if first. Updated Agent code/policies need their own
guarded publication; changing only the App/Job image does not update the Hosted runtime.

## Verification And Acceptance

```powershell
& .\.venv\Scripts\Activate.ps1
foreach ($template in @('infrastructure', 'application', 'runtime-roles')) {
    & "$HOME\.azure\bin\bicep.exe" build "infra/v2/$template.bicep" --outfile "infra/v2/$template.json"
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $template" }
}
python -c "import src"
python -m pytest tests/test_deployment_v2.py -o addopts= -q
python -m pytest tests/ -o addopts= -x
```

Offline tests check ARM write boundaries, symbolic-resource and literal-resource
forms, exact compiled RBAC scopes, completion ordering, existing network/DNS retention,
tag limits, initial install plus two updates, secret reuse, auth/schedule guards and
temporary-file cleanup. They do not perform Azure provisioning or prove registry pulls,
DNS routing, login/denial, PE Succeeded/Approved, actual log ingestion, Agent inference,
Archive access, email delivery or schedule readiness. Outputs deliberately keep
`applicationReady=false`; accept those customer paths before enabling automatic runs.